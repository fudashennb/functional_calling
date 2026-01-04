from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from memory.session_store import SessionState


@dataclass
class AgentOutput:
    kind: Literal["reply", "job"]
    speak_text: str
    request_id: str | None = None


from typing import List, Any
from tools.base import BaseToolbox


@dataclass
class AgentOutput:
    kind: Literal["reply", "job"]
    speak_text: str
    request_id: str | None = None


class Agent:
    name: str = "agent"

    def __init__(self, system_prompt: str = "") -> None:
        self.system_prompt = system_prompt
        self.toolboxes: List[BaseToolbox] = []

    def add_toolbox(self, toolbox: BaseToolbox) -> None:
        self.toolboxes.append(toolbox)

    def get_full_system_prompt(self) -> str:
        """
        组合基础提示词和所有已挂载工具箱的提示词片段。
        """
        prompt = self.system_prompt
        if self.toolboxes:
            prompt += "\n\n### 已挂载工具箱说明 ###\n"
            for tb in self.toolboxes:
                prompt += tb.get_prompt_fragment()
        return prompt

    def handle(self, *, query: str, session: SessionState) -> AgentOutput:
        raise NotImplementedError


