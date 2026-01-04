"""
SR Modbus SDK - 斯坦德机器人 Modbus 通信 SDK

提供 Modbus TCP/RTU 通信功能，用于控制 AGV 设备
"""

from .sr_modbus_sdk import SRModbusSdk
from .sr_modbus_model import (
    MovementState,
    MovementResult,
    ActionState,
    ActionResult,
    LocationState,
    BatteryInfo
)

__all__ = [
    'SRModbusSdk',
    'MovementState',
    'MovementResult',
    'ActionState',
    'ActionResult',
    'LocationState',
    'BatteryInfo'
]

__version__ = '1.0.0'
