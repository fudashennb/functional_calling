"""
会话存储（内存版）。

这里存三类信息：
- conversation：少量对话上下文（给LLM用）
- operational：运行态（当前是否有任务、当前request_id）
- cache：机器人状态缓存（可扩展TTL，这里先简单保存最后一次）
"""

from __future__ import annotations

import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ConversationMessage:
    role: str  # system/user/assistant/tool
    content: str | None = None
    tool_calls: List[Dict[str, Any]] | None = None
    tool_call_id: str | None = None
    ts: float = field(default_factory=lambda: time.time())

@dataclass
class SessionState:
    session_id: str
    lang: str = "zh"

    # 对话上下文（仅保留最近N条）
    conversation: List[ConversationMessage] = field(default_factory=list)
    max_conversation: int = 20  # ReAct 循环消息较多，调大一点

    # 运行态
    active_request_id: str | None = None
    
    # 新增：当前关联的 Plan ID (用于 Flow 状态持久化)
    active_plan_id: str | None = None
    
    # 外部引用的 JobManager，用于实现自愈检查
    _job_manager: Any = None 

    # 机器人状态缓存（结构化dict）
    robot_state_cache: Dict[str, Any] = field(default_factory=dict)
    robot_state_ts: float | None = None

    def push_message(self, role: str, content: str | None = None, tool_calls: List[Dict[str, Any]] | None = None, tool_call_id: str | None = None) -> None:
        self.conversation.append(ConversationMessage(
            role=role, 
            content=content, 
            tool_calls=tool_calls, 
            tool_call_id=tool_call_id
        ))
        if len(self.conversation) > self.max_conversation:
            self.conversation = self.conversation[-self.max_conversation :]

    def prune_history(self) -> None:
        """
        任务结束时的清洗：
        清空所有上下文，确保下一次任务是全新开始。
        这能极大减少 LLM 的困惑，并节省 Token。
        """
        if not self.conversation:
            return
        # 清空历史，因为状态通过 System Prompt 注入，无需依赖历史记忆
        self.conversation.clear()

    def is_busy(self) -> bool:
        """
        判断当前会话是否处于任务执行中。
        利用 JobManager 进行实时交叉比对，防止僵尸任务。
        """
        if not self.active_request_id:
            return False
            
        if not self._job_manager:
            # 如果没有 JobManager 引用，只能回退到简单判定
            return True
            
        job = self._job_manager.get(self.active_request_id)
        if job and job.status == "running":
            return True
            
        # 自愈：如果 JobManager 里的任务已经结束，但 Session 还记着 ID
        if self.active_request_id:
            logger.info(f"🔄 Session {self.session_id} 发现僵尸任务 ID {self.active_request_id}，正在执行自愈清理。")
            self.active_request_id = None
            
        return False


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[str, SessionState] = {}

    def get_or_create(self, session_id: str, *, lang: str = "zh") -> SessionState:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                s = SessionState(session_id=session_id, lang=lang)
                self._sessions[session_id] = s
            return s

    def get(self, session_id: str) -> SessionState | None:
        with self._lock:
            return self._sessions.get(session_id)
