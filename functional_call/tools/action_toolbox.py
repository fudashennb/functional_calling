from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from tools.base import BaseToolbox

if TYPE_CHECKING:
    import threading
    from tools.robot_client import RobotClient, EventEmitter

logger = logging.getLogger(__name__)

class ActionToolbox(BaseToolbox):
    """
    动作工具箱：负责顶升、放下、充电等物理动作。
    """
    def __init__(self, robot: RobotClient) -> None:
        super().__init__(robot)

    def execute_action(
        self, 
        action_id: int, 
        param1: int, 
        param2: int, 
        *, 
        timeout_s: int = 60, 
        emit: EventEmitter | None = None,
        stop_event: threading.Event | None = None
    ) -> None:
        self.robot.execute_action(action_id, param1, param2, timeout_s=timeout_s, emit=emit, stop_event=stop_event)

    def start_charge(
        self, 
        *, 
        timeout_s: int = 60, 
        emit: EventEmitter | None = None,
        stop_event: threading.Event | None = None
    ) -> None:
        self.robot.start_charge(timeout_s=timeout_s, emit=emit, stop_event=stop_event)

    def stop_charge(
        self, 
        *, 
        timeout_s: int = 60, 
        emit: EventEmitter | None = None,
        stop_event: threading.Event | None = None
    ) -> None:
        self.robot.stop_charge(timeout_s=timeout_s, emit=emit, stop_event=stop_event)

    def get_prompt_fragment(self) -> str:
        return (
            "动作工具箱 (ActionToolbox) 已启用：\n"
            "- execute_action(action_id, param1, param2): 执行特定动作。顶升动作遵循 4.11.X 规则。\n"
            "- start_charge(): 开始充电。\n"
            "- stop_charge(): 停止充电。\n"
        )
