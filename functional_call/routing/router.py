from __future__ import annotations

import logging
from typing import Literal

from memory.session_store import SessionState

from routing.local_model_router import LocalModelRouter


logger = logging.getLogger(__name__)

AgentName = Literal["planner", "command", "status", "diagnostics", "chat"]


class IntentRouter:
    """
    两段式路由（先规则，后可选本地模型/云端兜底）。

    当前先落地“规则 + 运行态优先”，后续再接入本地 BART/AdaptiveClassifier 增强。
    """

    def __init__(self, *, enable_local_models: bool = False) -> None:
        self._enable_local_models = enable_local_models
        self._local_router = LocalModelRouter() if enable_local_models else None

    def warm_up(self) -> None:
        """
        预热本地模型，使其立即加载到内存。
        """
        if self._local_router:
            self._local_router.warm_up()

    def route(self, *, query: str, session: SessionState) -> AgentName:
        q = (query or "").strip()
        if not q:
            return "chat"

        # 优先使用本地模型路由 (BART + AdaptiveClassifier)
        if self._local_router:
            logger.info(f"🔍 正在进行语义路由判定: \"{q}\"")
            try:
                labels = ["planner", "command", "status", "diagnostics", "chat"]
                res = self._local_router.route(text=q, lang=session.lang, labels=labels)
                
                if res.detail:
                    logger.info(f"✅ 语义路由结果：{res.agent}（{res.detail}）")
                
                if res.agent in labels:
                    return res.agent  # type: ignore[return-value]
            except Exception as e:
                logger.warning(f"❌ 本地路由模型执行失败，已回退到chat：{e}")

        # 如果本地模型未开启或执行异常，默认走 chat
        return "chat"


