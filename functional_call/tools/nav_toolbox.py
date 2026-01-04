from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from tools.base import BaseToolbox

if TYPE_CHECKING:
    from tools.robot_client import RobotClient, EventEmitter

logger = logging.getLogger(__name__)

class NavToolbox(BaseToolbox):
    """
    导航工具箱：负责机器人的移动与站点导航。
    """
    def __init__(self, robot: RobotClient) -> None:
        super().__init__(robot)

    def move_to_station(self, station_no: int, *, timeout_s: int = 120, emit: EventEmitter | None = None) -> None:
        """
        控制机器人移动到指定站点。
        """
        self.robot.move_to_station(station_no, timeout_s=timeout_s, emit=emit)

    def get_prompt_fragment(self) -> str:
        return (
            "导航工具箱 (NavToolbox) 已启用：\n"
            "- move_to_station(station_no): 控制机器人移动到指定站点，参数为整形数字。\n"
        )

