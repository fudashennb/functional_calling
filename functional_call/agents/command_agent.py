from __future__ import annotations

import logging
import re
import uuid
import threading
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
        # 确保 session 持有 JobManager 引用以进行自愈检查
        session._job_manager = self._job_manager

        # 1. 忙碌检查与拦截
        # 识别“停止”类关键词，允许强行复位
        is_stop_cmd = any(k in query for k in ["停止", "取消", "别动", "复位", "stop", "cancel"])
        
        if session.is_busy():
            if is_stop_cmd:
                logger.info(f"🛑 收到停止/取消指令，正在尝试中断 Session {session.session_id} 的活跃任务")
                self._job_manager.cancel_session_job(session.session_id)
                # 即使中断了，也需要继续往下走，因为 LLM 可能解析出对应的机器人底层 stop 动作
            else:
                return AgentOutput(kind="reply", speak_text="机器人正在忙，请等当前任务完成，或者对我说“停止”来中断任务。")

        # 2. LLM 解析
        actions = self._llm_actions(query, session.lang)

        if not actions:
            # 这里的兜底：如果用户说了停止但 LLM 没解析出来，我们也给一个成功的反馈，因为 JobManager 已经 cancel 了
            if is_stop_cmd:
                return AgentOutput(kind="reply", speak_text="已请求停止当前所有任务。")
            return AgentOutput(kind="reply", speak_text="我没能理解你的指令。你可以说：导航到站点一 / 开始充电 / 停止。")

        # LLM要求澄清
        if actions and actions[0][0] == "__clarify__":
            return AgentOutput(kind="reply", speak_text=actions[0][1].get("question", "你的指令不够明确，请再说一次。"))

        logger.info(f"🛠️ 最终待执行动作序列: {actions}")

        request_id = str(uuid.uuid4())
        session.active_request_id = request_id
        self._event_bus.ensure_stream(request_id)

        # 3. 定义托管运行器
        def _runner(stop_event: threading.Event) -> str | None:
            return self._execute_actions_managed(actions, request_id, session, stop_event)

        # 4. 定义清理钩子（关键：确保 session 状态无论如何都会重置）
        def _cleanup():
            if session.active_request_id == request_id:
                session.active_request_id = None
            logger.info(f"🧹 托管任务清理完成: {request_id}")

        self._job_manager.start(
            request_id=request_id,
            session_id=session.session_id,
            runner=_runner,
            on_cleanup=_cleanup
        )

        # 202 第一句播报
        first = "收到指令，开始执行。"
        return AgentOutput(kind="job", speak_text=first, request_id=request_id)

    def _execute_actions_managed(
        self, 
        actions: List[Tuple[str, dict]], 
        request_id: str, 
        session: SessionState, 
        stop_event: threading.Event
    ) -> str | None:
        """被 JobManager 托管的执行逻辑"""
        
        def _emit(ev_type: str, data: dict | None = None) -> None:
            speak = (data or {}).get("text") if isinstance(data, dict) else None
            speak_text = speak if speak else ""
            self._event_bus.emit(request_id, type=ev_type, speak_text=speak_text, data=data or {})

        try:
            _emit("started", {"text": "收到指令，开始执行。"})
            if self._voice_pusher:
                self._voice_pusher.push_plan(
                    speak_text="收到指令，开始执行。",
                    request_id=request_id,
                    session_id=session.session_id,
                )
            
            for tool, args in actions:
                # 每一小步执行前检查停止信号
                if stop_event.is_set():
                    logger.info(f"⏹️ 任务执行中检测到停止信号，中断后续动作: {tool}")
                    raise InterruptedError("任务已被用户取消")

                logger.info(f"▶️  正在执行工具: {tool}, 参数: {args}")
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
            
            completion_text = "任务执行完成。"
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
            if self._voice_pusher:
                self._voice_pusher.push_failed(
                    speak_text="任务已取消。",
                    request_id=request_id,
                    session_id=session.session_id,
                    data={"error": "cancelled by user"},
                )
            return "任务已取消。"
            
        except Exception as e:
            error_msg = f"任务执行失败：{e}"
            _emit("failed", {"text": error_msg})
            if self._voice_pusher:
                self._voice_pusher.push_failed(
                    speak_text=error_msg,
                    request_id=request_id,
                    session_id=session.session_id,
                    data={"error": str(e)},
                )
            raise # 抛给托管容器记录
