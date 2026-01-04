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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ConversationMessage:
    role: str  # system/user/assistant
    content: str
    ts: float = field(default_factory=lambda: time.time())


@dataclass
class SessionState:
    session_id: str
    lang: str = "zh"

    # 对话上下文（仅保留最近N条）
    conversation: List[ConversationMessage] = field(default_factory=list)
    max_conversation: int = 12

    # 运行态
    active_request_id: str | None = None

    # 机器人状态缓存（结构化dict）
    robot_state_cache: Dict[str, Any] = field(default_factory=dict)
    robot_state_ts: float | None = None

    def push_message(self, role: str, content: str) -> None:
        self.conversation.append(ConversationMessage(role=role, content=content))
        if len(self.conversation) > self.max_conversation:
            self.conversation = self.conversation[-self.max_conversation :]


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


