from __future__ import annotations

import logging
import uuid
import threading
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
        Planner 专属的 LLM 解析逻辑。
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
        session._job_manager = self._job_manager

        # 忙碌检查
        is_stop_cmd = any(k in query for k in ["停止", "取消", "复位", "stop", "cancel"])
        if session.is_busy():
            if is_stop_cmd:
                logger.info(f"🛑 收到停止指令，中断 Session {session.session_id} 的 Planner 任务")
                self._job_manager.cancel_session_job(session.session_id)
            else:
                return AgentOutput(kind="reply", speak_text="我正在规划并执行任务，请等一下，或者说“停止”。")

        # 优先使用 Planner 专属解析
        actions = self._llm_actions(query, session.lang)
        if not actions:
            actions = self._command_agent._llm_actions(query, session.lang)  # type: ignore[attr-defined]

        if not actions:
            if is_stop_cmd: return AgentOutput(kind="reply", speak_text="已请求停止当前任务。")
            return AgentOutput(kind="reply", speak_text="你的需求比较复杂，但我没能拆解出可执行步骤。")

        if actions and actions[0][0] == "__clarify__":
            return AgentOutput(kind="reply", speak_text=actions[0][1].get("question", "你的指令不够明确，请再说一次。"))

        request_id = str(uuid.uuid4())
        session.active_request_id = request_id
        self._event_bus.ensure_stream(request_id)

        def _runner(stop_event: threading.Event) -> str | None:
            return self._execute_plan_managed(actions, request_id, session, stop_event)

        def _cleanup():
            if session.active_request_id == request_id:
                session.active_request_id = None
            logger.info(f"🧹 Planner 任务清理完成: {request_id}")

        self._job_manager.start(
            request_id=request_id,
            session_id=session.session_id,
            runner=_runner,
            on_cleanup=_cleanup
        )
        return AgentOutput(kind="job", speak_text="我已拆解任务并开始执行。", request_id=request_id)

    def _execute_plan_managed(self, actions, request_id, session, stop_event) -> str | None:
        def _emit(ev_type: str, data: dict | None = None) -> None:
            speak = (data or {}).get("text") if isinstance(data, dict) else None
            speak_text = speak if speak else ""
            self._event_bus.emit(request_id, type=ev_type, speak_text=speak_text, data=data or {})

        plan_text = f"我已将任务拆解为 {len(actions)} 步，开始按顺序执行。"
        _emit("started", {"text": plan_text})
        if self._voice_pusher:
            self._voice_pusher.push_plan(
                speak_text=plan_text,
                request_id=request_id,
                session_id=session.session_id,
                data={"total_steps": len(actions)},
            )
        
        try:
            for idx, (tool, args) in enumerate(actions, start=1):
                if stop_event.is_set():
                    raise InterruptedError("规划任务已被用户取消")

                _emit("progress", {"text": f"开始执行第 {idx} 步：{tool}。", "step": idx, "tool": tool})
                
                try:
                    if tool == "move_to_station":
                        station_no = int(args.get("station_no"))
                        timeout_s = int(args.get("timeout_s", 120))
                        if self.nav:
                            self.nav.move_to_station(station_no, timeout_s=timeout_s, emit=_emit, stop_event=stop_event)
                    elif tool == "execute_action":
                        action_id = int(args.get("action_id"))
                        param1 = int(args.get("param1"))
                        param2 = int(args.get("param2"))
                        timeout_s = int(args.get("timeout_s", 60))
                        if self.action:
                            self.action.execute_action(action_id, param1, param2, timeout_s=timeout_s, emit=_emit, stop_event=stop_event)
                    elif tool == "start_charge":
                        timeout_s = int(args.get("timeout_s", 60))
                        if self.action:
                            self.action.start_charge(timeout_s=timeout_s, emit=_emit, stop_event=stop_event)
                    elif tool == "stop_charge":
                        timeout_s = int(args.get("timeout_s", 60))
                        if self.action:
                            self.action.stop_charge(timeout_s=timeout_s, emit=_emit, stop_event=stop_event)
                    _emit("step_done", {"text": f"第 {idx} 步已完成。", "step": idx})
                except InterruptedError:
                    raise
                except Exception as step_error:
                    fault_text = f"第 {idx} 步执行失败：{step_error}"
                    _emit("fault", {"text": fault_text, "step": idx, "error": str(step_error)})
                    if self._voice_pusher:
                        self._voice_pusher.push_fault(
                            speak_text=fault_text,
                            request_id=request_id,
                            session_id=session.session_id,
                            data={"step": idx, "tool": tool, "error": str(step_error)},
                        )
                    raise
            
            completion_text = "全部步骤执行完成。"
            _emit("completed", {"text": completion_text})
            if self._voice_pusher:
                self._voice_pusher.push_completed(
                    speak_text=completion_text,
                    request_id=request_id,
                    session_id=session.session_id,
                )
            return completion_text
        except InterruptedError:
            _emit("cancelled", {"text": "任务已取消。"})
            return "任务已取消。"
        except Exception as e:
            _emit("failed", {"text": f"规划任务最终失败：{e}"})
            raise
