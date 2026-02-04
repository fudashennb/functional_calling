#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
from pathlib import Path

# 将 functional_call 目录添加到 sys.path，以便导入 src 和 tools
current_dir = Path(__file__).resolve().parent
functional_call_dir = current_dir / "functional_call"
sys.path.append(str(functional_call_dir))

try:
    from core.config import load_settings
    from tools.robot_client import RobotClient
    from src.sr_modbus_model import SystemState, LocationState
except ImportError as e:
    print(f"❌ 导入失败: {e}")
    print("请确保在项目根目录下运行此脚本。")
    sys.exit(1)

def monitor_modbus():
    settings = load_settings()
    host = settings.modbus_host
    port = settings.modbus_port
    
    print(f"🔍 正在连接 Modbus: {host}:{port} ...")
    
    robot = None
    
    while True:
        try:
            if robot is None:
                try:
                    robot = RobotClient(host, port)
                    # 尝试一次读取以确认连接
                    robot._sdk.get_cur_system_state()
                    print("✅ 连接成功！开始监控 Modbus 状态 (按 Ctrl+C 退出)\n")
                    print("-" * 110)
                    print(f"{'时间':<10} | {'系统状态':<15} | {'定位状态':<15} | {'电量':<5} | {'当前位姿':<30} | {'连接'}")
                    print("-" * 110)
                except Exception as e:
                    print(f"⚠️ 连接失败，3秒后重试: {e}")
                    time.sleep(3)
                    continue

            sdk = robot._sdk
            
            try:
                # 获取各项状态
                sys_state = sdk.get_cur_system_state()
                loc_state = sdk.get_cur_locate_state()
                battery = sdk.get_battery_info()
                
                # 格式化输出
                timestamp = time.strftime("%H:%M:%S")
                sys_state_name = sys_state.name if hasattr(sys_state, 'name') else str(sys_state)
                loc_state_name = loc_state.name if hasattr(loc_state, 'name') else str(loc_state)
                battery_pct = f"{battery.percentage_electricity}%"
                
                # 获取位姿信息
                pose = sdk.get_cur_pose()
                pose_str = f"x:{pose.x:.2f}, y:{pose.y:.2f}, yaw:{pose.yaw:.2f}"
                
                # 检查连接状态 (假定能读取到数据即为 Connected)
                conn_status = "🟢 OK"
                
                print(f"{timestamp:<10} | {sys_state_name:<15} | {loc_state_name:<15} | {battery_pct:<5} | {pose_str:<30} | {conn_status}")
                
                # 如果有任务在运行，也可以显示
                move_info = sdk.get_movement_task_info()
                if move_info.state.value not in [0, 5]: # MT_NA or MT_FINISHED
                    print(f"  └─ 🚀 移动任务: {move_info.state.name}, 目标: {move_info.target_station}, 编号: {move_info.no}")
                
                action_info = sdk.get_action_task_info()
                if action_info.state.value not in [0, 5]: # AT_NA or AT_FINISHED
                    print(f"  └─ 🛠️ 动作任务: {action_info.state.name}, ID: {action_info.id}, 编号: {action_info.no}")

            except Exception as e:
                print(f"{time.strftime('%H:%M:%S'):<10} | ⚠️ 读取异常: {e}")
                # 出现异常可能是连接断开，尝试重连
                robot = None
            
            time.sleep(1.0)
            
        except KeyboardInterrupt:
            print("\n👋 已停止监控。")
            break
        except Exception as e:
            print(f"❌ 发生未捕获错误: {e}")
            time.sleep(3)

if __name__ == "__main__":
    monitor_modbus()
