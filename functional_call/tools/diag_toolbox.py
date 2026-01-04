from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from tools.base import BaseToolbox

if TYPE_CHECKING:
    from tools.robot_client import RobotClient

logger = logging.getLogger(__name__)

class DiagToolbox(BaseToolbox):
    """
    诊断工具箱：负责通信检查与故障排查。
    """
    def __init__(self, robot: RobotClient, host: str, port: int) -> None:
        super().__init__(robot)
        self.host = host
        self.port = port

    def check_connection(self) -> bool:
        """简单的 Modbus 通信测试"""
        try:
            self.robot.get_battery_info()
            return True
        except Exception:
            return False

    def get_prompt_fragment(self) -> str:
        return (
            "诊断工具箱 (DiagToolbox) 已启用：\n"
            f"- 能够诊断位于 {self.host}:{self.port} 的 Modbus 连接状态。\n"
        )

