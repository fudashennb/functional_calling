from __future__ import annotations

import logging

from agents.base import Agent, AgentOutput
from llm.dashscope_provider import DashScopeLLMProvider, DashScopeError
from memory.session_store import SessionState


logger = logging.getLogger(__name__)


class ChatAgent(Agent):
    name = "chat"

    def __init__(self, *, llm: DashScopeLLMProvider | None = None, system_prompt: str = "") -> None:
        super().__init__(system_prompt=system_prompt)
        self._llm = llm

    def handle(self, *, query: str, session: SessionState) -> AgentOutput:
        if not self._llm:
            return AgentOutput(
                kind="reply",
                speak_text="我目前只能执行机器人指令和查询状态。你可以说：导航到站点一、顶升到50、查看电池状态、开始充电。",
            )

        system = self.system_prompt
        try:
            ans = self._llm.chat(messages=[{"role": "system", "content": system}, {"role": "user", "content": query}])
        except DashScopeError as e:
            logger.warning(f"ChatAgent LLM失败：{e}")
            ans = "我暂时无法生成回答。你可以尝试用更明确的指令，例如：导航到站点一。"
        return AgentOutput(kind="reply", speak_text=ans)


