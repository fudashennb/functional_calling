from __future__ import annotations

import logging
import uuid
from typing import List, Tuple

from agents.base import Agent, AgentOutput
from agents.command_agent import CommandAgent
from core.event_bus import EventBus
from core.job_manager import JobManager
from core.voice_pusher import VoicePushNotifier
from memory.session_store import SessionState
from tools.nav_toolbox import NavToolbox
from tools.action_toolbox import ActionToolbox
from llm.dashscope_provider import DashScopeLLMProvider


logger = logging.getLogger(__name__)


class PlannerAgent(Agent):
    name = "planner"

    def __init__(
        self,
        *,
        event_bus: EventBus,
        job_manager: JobManager,
        command_agent: CommandAgent,
        voice_pusher: VoicePushNotifier | None = None,
        system_prompt: str = "",
        llm: DashScopeLLMProvider | None = None,
        nav_toolbox: NavToolbox | None = None,
        action_toolbox: ActionToolbox | None = None,
    ) -> None:
        super().__init__(system_prompt=system_prompt)
        self._event_bus = event_bus
        self._job_manager = job_manager
        self._command_agent = command_agent
        self._voice_pusher = voice_pusher
        self._llm = llm
        
        self.nav = nav_toolbox
        self.action = action_toolbox
        if nav_toolbox: self.add_toolbox(nav_toolbox)
        if action_toolbox: self.add_toolbox(action_toolbox)

    def _llm_actions(self, text: str, lang: str) -> List[Tuple[str, dict]] | None:
        """
        Planner 专属的 LLM 解析逻辑，使用自己的 system_prompt。
        如果解析不到，再尝试回退到 CommandAgent 的解析。
        """
        if not self._llm:
            return None
        
        system = self.get_full_system_prompt()
        user = (
            f"用户输入（{lang}）：{text}\n"
            "请根据规划专家角色，输出任务拆解的 JSON。\n"
        )
        
        try:
            raw = self._llm.chat(messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            # 复用 CommandAgent 的 JSON 解析逻辑，这里简单处理
            import json
            import re
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m: return None
            obj = json.loads(m.group(0))
            if obj.get("need_clarification"):
                return [("__clarify__", {"question": obj.get("question", "需要进一步明确。")})]
            
            actions = obj.get("actions")
            if not isinstance(actions, list): return None
            
            out: List[Tuple[str, dict]] = []
            for a in actions:
                tool = a.get("tool")
                args = a.get("args") or {}
                out.append((tool, args))
            return out or None
        except Exception as e:
            logger.warning(f"PlannerAgent LLM 解析失败: {e}")
            return None

    def handle(self, *, query: str, session: SessionState) -> AgentOutput:
        # 优先使用 Planner 专属解析
        actions = self._llm_actions(query, session.lang)
        
        # 兜底：使用 CommandAgent 的 LLM 解析
        if not actions:
            actions = self._command_agent._llm_actions(query, session.lang)  # type: ignore[attr-defined]

        if not actions:
            return AgentOutput(kind="reply", speak_text="你的需求比较复杂，但我没能拆解出可执行步骤。你可以换一种说法，例如：先导航到站点一，再顶升到50。")

        if actions and actions[0][0] == "__clarify__":
            return AgentOutput(kind="reply", speak_text=actions[0][1].get("question", "你的指令不够明确，请再说一次。"))

        request_id = str(uuid.uuid4())
        session.active_request_id = request_id
        self._event_bus.ensure_stream(request_id)

        def _emit(ev_type: str, data: dict | None = None) -> None:
            speak = (data or {}).get("text") if isinstance(data, dict) else None
            speak_text = speak if speak else ""
            self._event_bus.emit(request_id, type=ev_type, speak_text=speak_text, data=data or {})

        def _runner() -> str | None:
            plan_text = f"我已将任务拆解为 {len(actions)} 步，开始按顺序执行。"
            _emit("started", {"text": plan_text})
            # 推送"计划"消息到语音端
            if self._voice_pusher:
                self._voice_pusher.push_plan(
                    speak_text=plan_text,
                    request_id=request_id,
                    session_id=session.session_id,
                    data={"total_steps": len(actions)},
                )
            
            try:
                for idx, (tool, args) in enumerate(actions, start=1):
                    _emit("progress", {"text": f"开始执行第 {idx} 步：{tool}。", "step": idx, "tool": tool})
                    
                    try:
                        if tool == "move_to_station":
                            station_no = int(args.get("station_no"))
                            timeout_s = int(args.get("timeout_s", 120))
                            if self.nav:
                                self.nav.move_to_station(station_no, timeout_s=timeout_s, emit=_emit)
                        elif tool == "execute_action":
                            action_id = int(args.get("action_id"))
                            param1 = int(args.get("param1"))
                            param2 = int(args.get("param2"))
                            timeout_s = int(args.get("timeout_s", 60))
                            if self.action:
                                self.action.execute_action(action_id, param1, param2, timeout_s=timeout_s, emit=_emit)
                        elif tool == "start_charge":
                            timeout_s = int(args.get("timeout_s", 60))
                            if self.action:
                                self.action.start_charge(timeout_s=timeout_s, emit=_emit)
                        elif tool == "stop_charge":
                            timeout_s = int(args.get("timeout_s", 60))
                            if self.action:
                                self.action.stop_charge(timeout_s=timeout_s, emit=_emit)
                        _emit("step_done", {"text": f"第 {idx} 步已完成。", "step": idx})
                    except Exception as step_error:
                        # 某一步失败：推送故障消息
                        fault_text = f"第 {idx} 步执行失败：{step_error}"
                        _emit("fault", {"text": fault_text, "step": idx, "error": str(step_error)})
                        if self._voice_pusher:
                            self._voice_pusher.push_fault(
                                speak_text=fault_text,
                                request_id=request_id,
                                session_id=session.session_id,
                                data={"step": idx, "tool": tool, "error": str(step_error)},
                            )
                        raise  # 中断后续步骤

                completion_text = "全部步骤执行完成。"
                _emit("completed", {"text": completion_text})
                # 推送"完成"消息到语音端
                if self._voice_pusher:
                    self._voice_pusher.push_completed(
                        speak_text=completion_text,
                        request_id=request_id,
                        session_id=session.session_id,
                    )
                return completion_text
            except Exception as e:
                # 任务最终失败
                error_msg = f"任务执行失败：{e}"
                _emit("failed", {"text": error_msg})
                if self._voice_pusher:
                    self._voice_pusher.push_failed(
                        speak_text=error_msg,
                        request_id=request_id,
                        session_id=session.session_id,
                        data={"error": str(e)},
                    )
                raise

        self._job_manager.start(request_id=request_id, session_id=session.session_id, runner=_runner)
        return AgentOutput(kind="job", speak_text="我已拆解任务并开始执行。", request_id=request_id)


