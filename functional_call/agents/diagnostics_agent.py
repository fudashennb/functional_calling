from __future__ import annotations

import logging

from agents.base import Agent, AgentOutput
from memory.session_store import SessionState
from tools.diag_toolbox import DiagToolbox


logger = logging.getLogger(__name__)


class DiagnosticsAgent(Agent):
    name = "diagnostics"

    def __init__(self, *, diag_toolbox: DiagToolbox, system_prompt: str = "") -> None:
        super().__init__(system_prompt=system_prompt)
        self.diag = diag_toolbox
        self.add_toolbox(diag_toolbox)

    def handle(self, *, query: str, session: SessionState) -> AgentOutput:
        # 最小诊断：尝试读一个寄存器（通过工具箱）
        if self.diag.check_connection():
            return AgentOutput(kind="reply", speak_text="我已检查，当前Modbus通信正常。")
        else:
            # 给出可操作建议（中文）
            msg = (
                f"我无法通过Modbus获取机器人状态，可能是连接中断。"
                f"请检查：1) SSH隧道是否建立（本机端口 {self.diag.port} 是否监听）；"
                f"2) 配置的 MODBUS_HOST/MODBUS_PORT 是否正确（当前 {self.diag.host}:{self.diag.port}）；"
                "3) 机器人是否在线。"
            )
            return AgentOutput(kind="reply", speak_text=msg)


