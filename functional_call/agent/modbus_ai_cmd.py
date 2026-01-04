#!/usr/bin/env python
# -*- coding:utf-8 -*-
# @File: two_stations_move.py
# @Author: shiyixuan
# @Date: 2021/1/28
# @Describe: 在两个站点间来回移动10次，并在到达每个站点后等待5s

import random
import json
import sys
import os
import logging
from pathlib import Path

# 添加项目根目录到 Python 路径，以便导入 log_config
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 导入统一的日志配置
import log_config

# 导入 Modbus 配置
try:
    from .config import MODBUS_HOST, MODBUS_PORT
except ImportError:
    # 如果作为独立模块运行（直接导入），需要从当前目录导入
    import importlib.util
    import os
    config_path = os.path.join(os.path.dirname(__file__), 'config.py')
    spec = importlib.util.spec_from_file_location("agent_config", config_path)
    config_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config_module)
    MODBUS_HOST = config_module.MODBUS_HOST
    MODBUS_PORT = config_module.MODBUS_PORT

# 获取日志记录器（使用统一的日志系统）
logger = logging.getLogger(__name__)

# 导入 Modbus SDK（src 目录已在项目根目录，无需额外添加路径）
from src.sr_modbus_sdk import SRModbusSdk
from src.sr_modbus_model import MovementState
import time

class ModbusAICmd:
    def __init__(self):
        """初始化 Modbus AI 命令执行器
        
        从 config.py 读取配置：
        - MODBUS_HOST: AGV 设备 IP 地址
        - MODBUS_PORT: Modbus TCP 端口（默认502）
        """
        self.mb_server = SRModbusSdk()
        
        # 使用配置文件中的 Modbus 地址连接
        logger.info(f"🔌 连接到 Modbus 服务器: {MODBUS_HOST}:{MODBUS_PORT}")
        self.mb_server.connect_tcp(MODBUS_HOST, MODBUS_PORT)
        
        self.increment_no = random.randint(1, 10000)
        logger.info(f"✅ 初始化完成，任务编号起始值: {self.increment_no}")
        self.is_working = True

    def mv_to_station(self, station_no: int, timeout: int = 120):
        """移动到指定站点
        
        Args:
            station_no: 站点编号，从1开始
            timeout: 超时时间（秒），默认120秒
        """
        logger.info(f"开始移动到站点 {station_no}，超时时间: {timeout}秒")
        try:
            # 检查是否有正在运行的任务
            movement_task = self.mb_server.get_movement_task_info()
            if movement_task.state == MovementState.MT_RUNNING:
                logger.warning(f"⚠️ 检测到正在运行的任务（编号: {movement_task.no}），先取消旧任务")
                try:
                    self.mb_server.cancel_task()
                    # 等待任务取消完成（最多等待5秒）
                    for i in range(5):
                        time.sleep(1)
                        task_info = self.mb_server.get_movement_task_info()
                        if task_info.state != MovementState.MT_RUNNING:
                            logger.info(f"✅ 旧任务已取消，状态: {task_info.state}")
                            break
                except Exception as e:
                    logger.warning(f"⚠️ 取消旧任务失败: {e}，继续发送新任务")
            
            self.increment_no += 1
            task_no = self.increment_no
            logger.info(f"📤 发送移动任务 - 站点: {station_no}, 任务编号: {task_no}")
            self.mb_server.move_to_station_no(station_no, task_no)
            
            # 等待任务编号更新（最多等待3秒）
            logger.debug("⏳ 等待机器人接收任务...")
            for i in range(3):
                time.sleep(0.5)
                task_info = self.mb_server.get_movement_task_info()
                if task_info.no == task_no:
                    logger.info(f"✅ 机器人已接收任务，任务编号: {task_no}")
                    break
                elif i == 2:
                    logger.warning(f"⚠️ 任务编号未更新，期望: {task_no}, 实际: {task_info.no}，继续等待")
            
            self.mb_server.wait_movement_task_finish(task_no, timeout=timeout)
            logger.info("移动任务执行完成")
            return "执行成功"
        except TimeoutError as e:
            error_msg = f"移动任务超时: 站点{station_no}，已等待{timeout}秒。可能原因：机器人遇到障碍、路径阻塞或硬件故障。"
            logger.error(f"❌ {error_msg}")
            raise TimeoutError(error_msg)
        except ConnectionError as e:
            error_msg = str(e)
            # 区分连接错误类型
            if "长时间中断" in error_msg:
                # 连接长时间中断，任务可能仍在执行
                logger.error(f"❌ {error_msg}")
                logger.error("💡 提示: 任务可能仍在执行，请检查机器人状态或稍后查询任务状态")
                raise ConnectionError(
                    f"网络连接中断: 任务编号{task_no}可能仍在执行，但无法继续监控。"
                    f"请检查SSH隧道: ssh -f -N -L 1502:localhost:502 -p 2222 root@10.10.70.218"
                )
            elif "连接失败" in error_msg or "Failed to connect" in error_msg:
                logger.error(f"❌ Modbus连接失败: {error_msg}")
                logger.error("💡 提示: 请确保已建立SSH隧道: ssh -f -N -L 1502:localhost:502 -p 2222 root@10.10.70.218")
                raise ConnectionError(f"Modbus连接失败。请先建立SSH隧道: ssh -f -N -L 1502:localhost:502 -p 2222 root@10.10.70.218")
            else:
                # 其他连接错误
                logger.error(f"❌ Modbus连接错误: {error_msg}")
                raise ConnectionError(f"Modbus连接错误: {error_msg}")
        except RuntimeError as e:
            error_msg = f"移动任务执行失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            raise RuntimeError(error_msg)
        except Exception as e:
            error_msg = str(e)
            if "Connection" in error_msg or "Failed to connect" in error_msg:
                logger.error(f"❌ Modbus连接失败: {error_msg}")
                logger.error("💡 提示: 请确保已建立SSH隧道: ssh -f -N -L 1502:localhost:502 -p 2222 root@10.10.70.218")
                raise ConnectionError(f"Modbus连接失败。请先建立SSH隧道: ssh -f -N -L 1502:localhost:502 -p 2222 root@10.10.70.218")
            raise

    def execute_action(self, action_id: int, param1: int, param2: int, timeout: int = 60):
        """执行指定动作
        
        Args:
            action_id: 动作编号
            param1: 参数1
            param2: 参数2
            timeout: 超时时间（秒），默认60秒
        """
        logger.info(f"执行动作 {action_id}, 参数1: {param1}, 参数2: {param2}，超时时间: {timeout}秒")
        try:
            self.increment_no += 1
            self.mb_server.start_action_task_no(
                action_id, param1, param2, self.increment_no)
            self.mb_server.wait_action_task_finish(self.increment_no, timeout=timeout)
            logger.info("动作执行完成")
            return "执行成功"
        except TimeoutError as e:
            error_msg = f"动作任务超时: 动作{action_id}({param1},{param2})，已等待{timeout}秒。可能原因：动作执行异常或硬件故障。"
            logger.error(f"❌ {error_msg}")
            raise TimeoutError(error_msg)
        except RuntimeError as e:
            error_msg = f"动作任务执行失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            raise RuntimeError(error_msg)
        except Exception as e:
            error_msg = str(e)
            if "Connection" in error_msg or "Failed to connect" in error_msg:
                logger.error(f"❌ Modbus连接失败: {error_msg}")
                logger.error("💡 提示: 请确保已建立SSH隧道: ssh -f -N -L 1502:localhost:502 -p 2222 root@10.10.70.218")
                raise ConnectionError(f"Modbus连接失败。请先建立SSH隧道: ssh -f -N -L 1502:localhost:502 -p 2222 root@10.10.70.218")
            raise

    def execute_method(self, method_name: str):
        """执行指定方法"""
        logger.debug(f"执行方法: {method_name}")
        return getattr(self, method_name)
    
    # def input_robot_cmd(self, prompt: str) -> str:
    #     """处理用户输入的指令"""
    #     logger.info(f"收到用户指令: {prompt}")
    #     return json.dumps({
    #         "prompt": prompt,
    #     })
    
    def get_battery_info(self) -> str:
        """获取电池电量"""
        # 直接使用已连接的 mb_server 获取电池信息
        # SDK 路径已在 __init__ 中设置，mb_server 已经可以正常工作
        battery_percent = self.mb_server.get_battery_info()
        
        return json.dumps({
            "battery_info": {
                "percentage_electricity": battery_percent.percentage_electricity,
                "temperature": battery_percent.temperature,
                "state": str(battery_percent.state),
                "voltage": battery_percent.voltage,
                "current": battery_percent.current,
                "nominal_capacity": battery_percent.nominal_capacity
            }
        })
    
    def start_charge(self) -> str:
        """启动充电
        
        通过写入线圈地址9来启动充电。充电是即时控制命令，不需要等待完成。
        可以通过get_battery_info()查询电池状态来确认是否正在充电。
        
        Returns:
            str: JSON格式的执行结果
        """
        logger.info("🔌 启动充电...")
        try:
            # 检查是否已经在充电
            if self.mb_server.is_charge():
                logger.info("⚠️ 机器人已经在充电中")
                return json.dumps({
                    "status": "already_charging",
                    "message": "机器人已经在充电中"
                })
            
            # 发送充电命令（写入线圈地址9）
            logger.info("📤 发送充电命令...")
            self.mb_server.charge()
            
            # 轮询检查充电状态，每秒检查一次，直到成功或超时
            max_wait_time = 60  # 最大等待时间60秒
            poll_interval = 1  # 每秒轮询一次
            start_time = time.time()
            elapsed = 0
            
            logger.info(f"⏳ 开始轮询检查充电状态（最多等待 {max_wait_time} 秒）...")
            while elapsed < max_wait_time:
                time.sleep(poll_interval)
                elapsed = int(time.time() - start_time)
                
                # 检查充电状态
                if self.mb_server.is_charge():
                    logger.info(f"✅ 充电已成功启动（耗时 {elapsed} 秒）")
                    return json.dumps({
                        "status": "success",
                        "message": f"充电已成功启动（耗时 {elapsed} 秒）"
                    })
                
                # 每5秒记录一次日志
                if elapsed % 5 == 0:
                    logger.info(f"⏳ 等待充电启动中... ({elapsed}/{max_wait_time}秒)")
            
            # 超时仍未成功
            logger.warning(f"⚠️ 等待 {max_wait_time} 秒后，充电状态仍未确认")
            return json.dumps({
                "status": "sent",
                "message": f"充电命令已发送，但等待 {max_wait_time} 秒后充电状态仍未确认，请稍后查询电池状态确认"
            })
                
        except ConnectionError as e:
            error_msg = str(e)
            logger.error(f"❌ Modbus连接失败: {error_msg}")
            logger.error("💡 提示: 请确保已建立SSH隧道: ssh -f -N -L 1502:localhost:502 -p 2222 root@10.10.70.218")
            raise ConnectionError(f"Modbus连接失败。请先建立SSH隧道: ssh -f -N -L 1502:localhost:502 -p 2222 root@10.10.70.218")
        except Exception as e:
            error_msg = f"启动充电失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            raise RuntimeError(error_msg)

    def stop_charge(self) -> str:
        """停止充电
        
        通过写入线圈地址10来停止充电。
        
        Returns:
            str: JSON格式的执行结果
        """
        logger.info("🔌 停止充电...")
        try:
            # 检查是否正在充电
            if not self.mb_server.is_charge():
                logger.info("⚠️ 机器人未在充电")
                return json.dumps({
                    "status": "not_charging",
                    "message": "机器人未在充电"
                })
            
            # 发送停止充电命令（写入线圈地址10）
            logger.info("📤 发送停止充电命令...")
            self.mb_server.stop_charge()
            
            # 轮询检查充电状态，每秒检查一次，直到成功或超时
            max_wait_time = 60  # 最大等待时间60秒
            poll_interval = 1  # 每秒轮询一次
            start_time = time.time()
            elapsed = 0
            
            logger.info(f"⏳ 开始轮询检查充电状态（最多等待 {max_wait_time} 秒）...")
            while elapsed < max_wait_time:
                time.sleep(poll_interval)
                elapsed = int(time.time() - start_time)
                
                # 检查充电状态
                if not self.mb_server.is_charge():
                    logger.info(f"✅ 充电已成功停止（耗时 {elapsed} 秒）")
                    return json.dumps({
                        "status": "success",
                        "message": f"充电已成功停止（耗时 {elapsed} 秒）"
                    })
                
                # 每5秒记录一次日志
                if elapsed % 5 == 0:
                    logger.info(f"⏳ 等待充电停止中... ({elapsed}/{max_wait_time}秒)")
            
            # 超时仍未成功
            logger.warning(f"⚠️ 等待 {max_wait_time} 秒后，充电状态仍未确认")
            return json.dumps({
                "status": "sent",
                "message": f"停止充电命令已发送，但等待 {max_wait_time} 秒后充电状态仍未确认，请稍后查询电池状态确认"
            })
                
        except ConnectionError as e:
            error_msg = str(e)
            logger.error(f"❌ Modbus连接失败: {error_msg}")
            logger.error("💡 提示: 请确保已建立SSH隧道: ssh -f -N -L 1502:localhost:502 -p 2222 root@10.10.70.218")
            raise ConnectionError(f"Modbus连接失败。请先建立SSH隧道: ssh -f -N -L 1502:localhost:502 -p 2222 root@10.10.70.218")
        except Exception as e:
            error_msg = f"停止充电失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            raise RuntimeError(error_msg)
    
    def terminate_chat(self, terminal_message: str) -> str:
        """终止聊天会话"""
        self.is_working = False
        logger.info(f"终止会话: {terminal_message}")
        return json.dumps({"terminate": "terminate successfully!"})
    
    # AGV基本信息相关函数
    def get_agv_access_time(self) -> str:
        """获取AGV接入系统时间"""
        return json.dumps({
            "access_time": "2024-01-01 08:00:00",
            "duration": "3个月"
        })

    def get_agv_device_info(self) -> str:
        """获取AGV设备信息"""
        return json.dumps({
            "device_type": "叉车式AGV",
            "manufacturer": "斯坦德机器人",
            "speed": "1.2m/s",
            "asset_status": "正常运行",
            "current_area": "原料仓",
            "current_map": "1号仓库"
        })

    def get_agv_statistics(self) -> str:
        """获取AGV统计信息"""
        return json.dumps({
            "current_area_agv_count": 5,
            "current_map_agv_count": 8,
            "current_area_agv_types": 2,
            "current_map_agv_types": 3
        })

    # AGV运行状态相关函数
    def get_agv_task_status(self) -> str:
        """获取AGV任务状态"""
        return json.dumps({
            "current_status": "执行中",
            "today_completed_tasks": 50,
            "today_completed_commands": 150
        })

    def get_agv_weekly_trends(self) -> str:
        """获取AGV周趋势数据"""
        return json.dumps({
            "wait_empty_location_trend": [10, 12, 8, 15, 9, 11, 13],
            "wait_idle_agv_trend": [5, 8, 6, 9, 7, 8, 10],
            "system_scheduling_trend": [2, 3, 2.5, 3.5, 2.8, 3.2, 2.9],
            "execution_time_trend": [25, 28, 24, 30, 27, 29, 26],
            "task_trend": [45, 48, 42, 50, 47, 49, 46],
            "command_trend": [135, 144, 126, 150, 141, 147, 138]
        })

    def get_agv_performance(self) -> str:
        """获取AGV性能指标"""
        return json.dumps({
            "yesterday_utilization": 85.5,
            "yesterday_failure_rate": 2.3,
            "yesterday_efficiency": 92.8,
            "today_failures": 3,
            "today_failure_duration": "2小时15分钟"
        })

    # AGV电池相关函数
    def get_agv_charging_info(self) -> str:
        """获取AGV充电信息"""
        return json.dumps({
            "today_charging_duration": "5小时30分钟",
            "today_charging_times": 4,
            "battery_capacity": 100,
            "battery_level": 75,
            "battery_usage_time": "180天",
            "battery_remaining_life": "540天",
            "battery_voltage": 48.5,
            "battery_temperature": 25
        })

    def get_battery_warnings(self) -> str:
        """获取电池警告信息"""
        return json.dumps({
            "temperature_warnings": 2,
            "battery_fault_warnings": 1,
            "temperature_diff_warnings": 0,
            "capacity_warnings": 1,
            "voltage_threshold_warnings": 0
        })

    # AGV工单相关函数
    def get_work_order_info(self) -> str:
        """获取工单信息"""
        return json.dumps({
            "pending_orders": 15,
            "order_types": ["维修工单", "保养工单", "故障工单", "巡检工单"],
            "monthly_statistics": {
                "typical_issues": 25,
                "safety_orders": 10,
                "task_orders": 150,
                "abnormal_orders": 8,
                "maintenance_orders": 12
            }
        })

    def get_monthly_order_trends(self) -> str:
        """获取月度工单趋势"""
        return json.dumps({
            "total_orders_trend": [120, 135, 128, 142, 138, 145, 132, 140, 136, 143, 139, 148, 134, 141, 137, 144, 140, 146, 133, 139, 135, 142, 138, 145, 131, 137, 133, 140, 136, 143],
            "safety_orders_trend": [8, 10, 9, 11, 10, 12, 9, 11, 10, 12, 11, 13, 10, 12, 11, 13, 12, 14, 11, 13, 12, 14, 13, 15, 12, 14, 13, 15, 14, 16],
            "typical_issues_trend": [20, 22, 21, 23, 22, 24, 21, 23, 22, 24, 23, 25, 22, 24, 23, 25, 24, 26, 23, 25, 24, 26, 25, 27, 24, 26, 25, 27, 26, 28],
            "task_orders_trend": [92, 103, 98, 108, 106, 109, 102, 106, 104, 107, 105, 110, 102, 105, 103, 106, 104, 106, 99, 101, 99, 102, 100, 103, 95, 97, 95, 98, 96, 99]
        })

    def get_weekly_area_performance(self) -> str:
        """获取各区域周度性能数据"""
        return json.dumps({
            "highest_utilization_area": "原料仓",
            "lowest_utilization_area": "成品仓",
            "highest_failure_rate_area": "半成品仓",
            "lowest_failure_rate_area": "原料仓",
            "highest_efficiency_area": "原料仓",
            "lowest_efficiency_area": "成品仓",
            "highest_failure_rate_fluctuation_area": "半成品仓"
        })

    def get_weekly_factory_trends(self) -> str:
        """获取工厂周度趋势"""
        return json.dumps({
            "factory_task_trend": [45, 48, 42, 50, 47, 49, 46],
            "factory_command_trend": [135, 144, 126, 150, 141, 147, 138]
        })

    def get_weekly_warehouse_trends(self) -> str:
        """获取外仓周度趋势"""
        return json.dumps({
            "warehouse_task_trend": [25, 28, 24, 30, 27, 29, 26],
            "warehouse_command_trend": [75, 84, 66, 90, 81, 87, 78]
        })

    def get_weekly_efficiency_trends(self) -> str:
        """获取周度效率趋势"""
        return json.dumps({
            "utilization_trend": [85.5, 86.2, 84.8, 87.1, 85.9, 86.5, 85.2],
            "failure_rate_trend": [2.3, 2.1, 2.5, 2.0, 2.4, 2.2, 2.6],
            "failure_count_trend": [3, 2, 4, 2, 3, 2, 3],
            "efficiency_trend": [92.8, 93.1, 92.3, 93.5, 92.7, 93.2, 92.5]
        })

    def get_battery_temperature_warnings(self) -> str:
        """获取电池温度预警信息"""
        return json.dumps({
            "charging_temperature_warnings": 2,
            "battery_fault_warnings": 1,
            "charging_temperature_diff_warnings": 0,
            "battery_capacity_warnings": 1,
            "voltage_threshold_warnings": 0
        })

    def get_battery_usage_info(self) -> str:
        """获取电池使用信息"""
        return json.dumps({
            "usage_duration": "180天",
            "remaining_life": "540天",
            "voltage": 48.5,
            "temperature": 25
        })

    def get_today_work_orders(self) -> str:
        """获取今日工单信息"""
        return json.dumps({
            "pending_orders": 15,
            "order_types": ["维修工单", "保养工单", "故障工单", "巡检工单"]
        })

    def get_monthly_work_order_types(self) -> str:
        """获取月度工单类型统计"""
        return json.dumps({
            "typical_problem_orders": 25,
            "safety_orders": 10,
            "task_orders": 150,
            "abnormal_orders": 8,
            "maintenance_orders": 12
        })

    def get_monthly_work_order_trends(self) -> str:
        """获取月度工单趋势"""
        return json.dumps({
            "total_trend": [120, 135, 128, 142],
            "safety_trend": [8, 10, 9, 11],
            "typical_problem_trend": [20, 22, 21, 23],
            "task_trend": [92, 103, 98, 108]
        })

    def get_agv_current_location(self) -> str:
        """获取AGV当前位置信息"""
        return json.dumps({
            "current_area": "原料仓",
            "current_map": "1号仓库"
        })

    def get_agv_area_statistics(self) -> str:
        """获取AGV区域统计信息"""
        return json.dumps({
            "current_area_devices": 5,
            "current_map_devices": 8,
            "current_area_types": 2,
            "current_map_types": 3
        })

    def get_today_task_statistics(self) -> str:
        """获取今日任务统计"""
        return json.dumps({
            "completed_tasks": 50,
            "completed_commands": 150
        })

    def get_today_charging_statistics(self) -> str:
        """获取今日充电统计"""
        return json.dumps({
            "charging_duration": "5小时30分钟",
            "charging_times": 4
        })

    def get_today_failure_statistics(self) -> str:
        """获取今日故障统计"""
        return json.dumps({
            "failure_count": 3,
            "failure_duration": "2小时15分钟"
        })

    def get_yesterday_performance(self) -> str:
        """获取昨日性能统计"""
        return json.dumps({
            "utilization_rate": 85.5,
            "failure_rate": 2.3,
            "efficiency_rate": 92.8
        })

    


