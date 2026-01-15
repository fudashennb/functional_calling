from __future__ import annotations

import logging
from typing import Literal

from memory.session_store import SessionState

from routing.llm_router import LLMRouter
from llm.dashscope_provider import DashScopeLLMProvider


logger = logging.getLogger(__name__)

AgentName = Literal["planner", "command", "status"]


class IntentRouter:
    """
    LLM 驱动的路由分发器（DashScope 版）。
    """

    def __init__(self, llm: DashScopeLLMProvider) -> None:
        self._llm_router = LLMRouter(llm)

    def warm_up(self) -> None:
        """
        LLM 路由无需预热本地模型。
        """
        pass

    def route(self, *, query: str, session: SessionState) -> AgentName:
        q = (query or "").strip()
        if not q:
            return "status"

        # 使用 LLM 进行意图分发
        res = self._llm_router.route(q)
        return res.agent  # type: ignore[return-value]


