from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from tools.base import BaseToolbox

if TYPE_CHECKING:
    import threading
    from tools.robot_client import RobotClient, EventEmitter

logger = logging.getLogger(__name__)

class NavToolbox(BaseToolbox):
    """
    导航工具箱：负责机器人的移动与站点导航。
    """
    def __init__(self, robot: RobotClient) -> None:
        super().__init__(robot)

    def move_to_station(
        self, 
        station_no: int, 
        *, 
        timeout_s: int = 120, 
        emit: EventEmitter | None = None,
        stop_event: threading.Event | None = None
    ) -> None:
        """
        控制机器人移动到指定站点。
        """
        # 增加心跳日志 (Heartbeat) - 在 Agent 层面已经由 EventBus 处理，但这里可以加强
        # 注意：实际的心跳逻辑应该在 RobotClient 中实现，或者在这里包装。
        # 这里假设 RobotClient.move_to_station 已经是阻塞的且有基本的重试逻辑。
        # 我们可以在调用前后增加详细日志。
        if emit:
            emit("started", {"text": f"开始导航前往 {station_no} 号站点..."})
            
        try:
            self.robot.move_to_station(station_no, timeout_s=timeout_s, emit=emit, stop_event=stop_event)
            if emit:
                emit("completed", {"text": f"已到达 {station_no} 号站点。"})
        except Exception as e:
            if emit:
                emit("failed", {"text": f"导航失败: {e}"})
            raise

    def get_prompt_fragment(self) -> str:
        return (
            "导航工具箱 (NavToolbox) 已启用：\n"
            "- move_to_station(station_no): 控制机器人移动到指定站点，参数为整形数字。\n"
        )
