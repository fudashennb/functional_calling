from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from tools.base import BaseToolbox

if TYPE_CHECKING:
    from tools.robot_client import RobotClient

logger = logging.getLogger(__name__)

class StatusToolbox(BaseToolbox):
    """
    状态工具箱：负责查询电量、位置、任务进度。
    """
    def __init__(self, robot: RobotClient) -> None:
        super().__init__(robot)

    def get_battery_info(self) -> dict:
        return self.robot.get_battery_info()

    def get_movement_task_info(self) -> dict:
        return self.robot.get_movement_task_info()

    def get_action_task_info(self) -> dict:
        return self.robot.get_action_task_info()

    def is_charging(self) -> bool:
        return self.robot.is_charging()

    def get_prompt_fragment(self) -> str:
        return (
            "状态工具箱 (StatusToolbox) 已启用：\n"
            "- get_battery_info(): 获取电量、温度、电压等电池信息。\n"
            "- get_movement_task_info(): 获取当前导航任务的状态和进度。\n"
            "- get_action_task_info(): 获取当前动作任务的状态和进度。\n"
            "- is_charging(): 判断是否正在充电。\n"
        )

