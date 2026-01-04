from __future__ import annotations

import logging
import re
import uuid
from typing import List, Optional, Tuple

from agents.base import Agent, AgentOutput
from core.event_bus import EventBus
from core.job_manager import JobManager
from core.voice_pusher import VoicePushNotifier
from memory.session_store import SessionState
from tools.nav_toolbox import NavToolbox
from tools.action_toolbox import ActionToolbox
from llm.dashscope_provider import DashScopeLLMProvider, DashScopeError


logger = logging.getLogger(__name__)


class CommandAgent(Agent):
    name = "command"

    def __init__(
        self,
        *,
        event_bus: EventBus,
        job_manager: JobManager,
        llm: DashScopeLLMProvider | None = None,
        voice_pusher: VoicePushNotifier | None = None,
        system_prompt: str = "",
        nav_toolbox: NavToolbox | None = None,
        action_toolbox: ActionToolbox | None = None,
    ) -> None:
        super().__init__(system_prompt=system_prompt)
        self._event_bus = event_bus
        self._job_manager = job_manager
        self._llm = llm
        self._voice_pusher = voice_pusher
        
        self.nav = nav_toolbox
        self.action = action_toolbox
        if nav_toolbox: self.add_toolbox(nav_toolbox)
        if action_toolbox: self.add_toolbox(action_toolbox)

    # ---------- LLM 解析：输出JSON动作序列 ----------
    def _llm_actions(self, text: str, lang: str) -> List[Tuple[str, dict]] | None:
        if not self._llm:
            return None
        system = self.get_full_system_prompt()
        user = (
            f"用户输入（{lang}）：{text}\n"
            "请输出：\n"
            "1) 如果能执行：{\"actions\":[{\"tool\":\"...\",\"args\":{...}}, ...]}\n"
            "2) 如果需要澄清：{\"need_clarification\": true, \"question\": \"...\"}\n"
        )
        try:
            raw = self._llm.chat(messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        except DashScopeError as e:
            logger.warning(f"LLM解析失败（DashScope）：{e}")
            return None

        import json

        try:
            obj = json.loads(raw)
        except Exception:
            # 有时会带 ```json ... ```，做一次提取
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                return None
            try:
                obj = json.loads(m.group(0))
            except Exception:
                return None

        if obj.get("need_clarification"):
            q = obj.get("question") or "你的指令不够明确，请你再说一次。"
            return [("__clarify__", {"question": q})]

        actions = obj.get("actions")
        if not isinstance(actions, list) or not actions:
            return None
        out: List[Tuple[str, dict]] = []
        for a in actions:
            tool = a.get("tool")
            args = a.get("args") or {}
            if tool not in {"move_to_station", "execute_action", "start_charge", "stop_charge"}:
                continue
            out.append((tool, args))
        return out or None

    # ---------- 执行 ----------
    def handle(self, *, query: str, session: SessionState) -> AgentOutput:
        actions = self._llm_actions(query, session.lang)

        if not actions:
            return AgentOutput(kind="reply", speak_text="我没能理解你的指令。你可以说：导航到站点一 / 顶升到50 / 查看电池状态 / 开始充电。")

        # LLM要求澄清
        if actions and actions[0][0] == "__clarify__":
            return AgentOutput(kind="reply", speak_text=actions[0][1].get("question", "你的指令不够明确，请再说一次。"))

        logger.info(f"🛠️ 最终待执行动作序列: {actions}")

        request_id = str(uuid.uuid4())
        session.active_request_id = request_id
        self._event_bus.ensure_stream(request_id)

        def _emit(ev_type: str, data: dict | None = None) -> None:
            # data里常见包含 {"text": "..."}，统一转 speak_text
            speak = (data or {}).get("text") if isinstance(data, dict) else None
            speak_text = speak if speak else ""
            self._event_bus.emit(request_id, type=ev_type, speak_text=speak_text, data=data or {})

        def _runner() -> str | None:
            try:
                _emit("started", {"text": "收到指令，开始执行。"})
                # 推送"计划"消息到语音端
                if self._voice_pusher:
                    self._voice_pusher.push_plan(
                        speak_text="收到指令，开始执行。",
                        request_id=request_id,
                        session_id=session.session_id,
                    )
                
                for tool, args in actions:
                    logger.info(f"▶️  正在执行工具: {tool}, 参数: {args}")
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
                
                _emit("completed", {"text": "任务执行完成。"})
                # 推送"完成"消息到语音端
                if self._voice_pusher:
                    self._voice_pusher.push_completed(
                        speak_text="任务执行完成。",
                        request_id=request_id,
                        session_id=session.session_id,
                    )
                return "任务执行完成。"
            except Exception as e:
                error_msg = f"任务执行失败：{e}"
                _emit("failed", {"text": error_msg})
                # 推送"失败"消息到语音端
                if self._voice_pusher:
                    self._voice_pusher.push_failed(
                        speak_text=error_msg,
                        request_id=request_id,
                        session_id=session.session_id,
                        data={"error": str(e)},
                    )
                raise

        self._job_manager.start(
            request_id=request_id,
            session_id=session.session_id,
            runner=_runner,
        )

        # 202 第一句播报
        first = "收到指令，开始执行。"
        return AgentOutput(kind="job", speak_text=first, request_id=request_id)


