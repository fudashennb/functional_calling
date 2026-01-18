from app.tools.base import ToolRegistry
from tools.nav_toolbox import NavToolbox
from tools.action_toolbox import ActionToolbox
from tools.status_toolbox import StatusToolbox
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tools.robot_client import EventEmitter
    import threading

logger = logging.getLogger(__name__)

# Instances to be injected later
_nav_toolbox: NavToolbox = None
_action_toolbox: ActionToolbox = None
_status_toolbox: StatusToolbox = None

def initialize_tools(nav: NavToolbox, action: ActionToolbox, status: StatusToolbox):
    global _nav_toolbox, _action_toolbox, _status_toolbox
    _nav_toolbox = nav
    _action_toolbox = action
    _status_toolbox = status

@ToolRegistry.register(name="move_to_station", description="导航机器人到指定站点。")
async def move_to_station(station_no: int, timeout_s: int = 120, emit: 'EventEmitter' = None, stop_event: 'threading.Event' = None):
    if not _nav_toolbox:
        return "错误：导航工具未初始化。"
    try:
        _nav_toolbox.move_to_station(station_no, timeout_s=timeout_s, emit=emit, stop_event=stop_event)
        return f"成功到达站点 {station_no}。"
    except Exception as e:
        logger.error(f"导航失败: {e}")
        return f"错误：导航失败。{str(e)}"

@ToolRegistry.register(name="execute_action", description="执行特定的硬件动作（如顶升、降下）。")
async def execute_action(action_id: int, param1: int, param2: int, timeout_s: int = 60, emit: 'EventEmitter' = None, stop_event: 'threading.Event' = None):
    if not _action_toolbox:
        return "错误：动作工具未初始化。"
    try:
        _action_toolbox.execute_action(action_id, param1, param2, timeout_s=timeout_s, emit=emit, stop_event=stop_event)
        return f"动作 {action_id} 执行成功。"
    except Exception as e:
        logger.error(f"动作执行失败: {e}")
        return f"错误：动作 {action_id} 执行失败。{str(e)}"

@ToolRegistry.register(name="start_charge", description="开始给机器人充电。注意：物理状态转变需一定时间，timeout_s 建议设为 40。")
async def start_charge(timeout_s: int = 40, emit: 'EventEmitter' = None, stop_event: 'threading.Event' = None):
    if not _action_toolbox:
        return "错误：动作工具未初始化。"
    try:
        _action_toolbox.start_charge(timeout_s=timeout_s, emit=emit, stop_event=stop_event)
        return "成功开始充电。"
    except Exception as e:
        logger.error(f"开始充电失败: {e}")
        return f"错误：开始充电失败。{str(e)}"

@ToolRegistry.register(name="stop_charge", description="停止给机器人充电。timeout_s 建议设为 40。")
async def stop_charge(timeout_s: int = 40, emit: 'EventEmitter' = None, stop_event: 'threading.Event' = None):
    if not _action_toolbox:
        return "错误：动作工具未初始化。"
    try:
        _action_toolbox.stop_charge(timeout_s=timeout_s, emit=emit, stop_event=stop_event)
        return "成功停止充电。"
    except Exception as e:
        logger.error(f"停止充电失败: {e}")
        return f"错误：停止充电失败。{str(e)}"

@ToolRegistry.register(name="get_robot_status", description="获取机器人当前状态，包括电量、位置和任务信息。")
def get_robot_status():
    if not _status_toolbox:
        return "错误：状态工具未初始化。"
    try:
        battery = _status_toolbox.get_battery_info()
        movement = _status_toolbox.get_movement_task_info()
        action = _status_toolbox.get_action_task_info()
        is_charging = _status_toolbox.is_charging()
        
        return (f"机器人状态报告：\n"
                f"- 电池电量：{battery}\n"
                f"- 是否充电：{is_charging}\n"
                f"- 移动任务：{movement}\n"
                f"- 动作任务：{action}")
    except Exception as e:
        logger.error(f"获取状态失败: {e}")
        return f"错误：获取状态失败。{str(e)}"
