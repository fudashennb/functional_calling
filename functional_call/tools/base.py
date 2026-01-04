from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ToolResult:
    ok: bool
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    retryable: bool = False


class BaseToolbox:
    """
    所有工具箱的基类。
    """
    def __init__(self, robot: Any) -> None:
        self.robot = robot

    def get_prompt_fragment(self) -> str:
        """
        返回该工具箱的提示词片段，用于动态注入 Agent 的 system_prompt。
        """
        return ""


