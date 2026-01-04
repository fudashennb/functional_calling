#!/usr/bin/env python
# -*- coding:utf-8 -*-
# @File: sr_modbus_sdk.py
# @Author: shiyixuan
# @Date: 2021/1/15
# @Describe:

from pymodbus.client.sync import ModbusTcpClient, ModbusSerialClient
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder, BinaryPayloadBuilder
import time
import binascii
import logging
from .sr_modbus_model import *

# 创建logger
logger = logging.getLogger(__name__)


class SRModbusSdk:
    def __init__(self):
        self._client = None
        self._ip = None
        self._port = None

    def connect_tcp(self, ip, port=502):
        """
        用modbus-TCP连车辆
        :param ip: 车辆ip
        :param port: 车辆端口号
        :return:
        """
        self._ip = ip
        self._port = port
        self._client = ModbusTcpClient(host=ip, port=port)
        ret = self._client.connect()
        if ret:
            logger.info(f"✅ Modbus TCP连接成功: {ip}:{port}")
        else:
            logger.error(f"❌ Modbus TCP连接失败: {ip}:{port}")
            return
    
    def _check_and_reconnect(self, max_retries=3):
        """
        检查连接状态，如果断开则尝试重连
        :param max_retries: 最大重试次数
        :return: True if connected, False otherwise
        """
        if self._client is None:
            logger.error("❌ Modbus客户端未初始化")
            return False
        
        # 检查连接状态
        if hasattr(self._client, 'is_socket_open') and self._client.is_socket_open():
            return True
        
        # 连接已断开，尝试重连
        if self._ip is None or self._port is None:
            logger.error("❌ 无法重连：未保存连接信息")
            return False
        
        logger.warning(f"⚠️ Modbus连接已断开，尝试重连... ({self._ip}:{self._port})")
        for i in range(max_retries):
            try:
                # 关闭旧连接
                if hasattr(self._client, 'close'):
                    self._client.close()
                
                # 创建新连接
                self._client = ModbusTcpClient(host=self._ip, port=self._port)
                ret = self._client.connect()
                
                if ret:
                    logger.info(f"✅ Modbus重连成功 ({i+1}/{max_retries})")
                    return True
                else:
                    logger.warning(f"⚠️ Modbus重连失败 ({i+1}/{max_retries})")
                    time.sleep(1)  # 等待1秒后重试
            except Exception as e:
                logger.warning(f"⚠️ Modbus重连异常 ({i+1}/{max_retries}): {e}")
                time.sleep(1)
        
        logger.error(f"❌ Modbus重连失败，已尝试{max_retries}次")
        return False

    def connect_rtu(self, port, baudrate=115200, parity="N"):
        """
        用modbus-RTU连车辆
        :param port: USB Serial port（端口）
        :param baudrate: 波特率
        :param parity: 奇偶校验
        :return:
        """
        self._client = ModbusSerialClient(method="rtu", port=port, stopbits=1, bytesize=8,
                                          parity=parity, baudrate=baudrate)
        ret = self._client.connect()
        if ret:
            logger.info(f"✅ Modbus RTU连接成功: {port}, 波特率: {baudrate}")
        else:
            logger.error(f"❌ Modbus RTU连接失败: {port}, 波特率: {baudrate}")
            return

    def wait_movement_task_finish(self, no=0, timeout=120):
        """
        阻塞等待任务结束，适用于站点移动、位置移动
        :param no: 任务编号
        :param timeout: 超时时间（秒），默认120秒
        :return: [移动任务结果, 移动任务结果值]
        :raises TimeoutError: 任务超时
        :raises RuntimeError: 任务错误或失败
        """
        start_time = time.time()
        i = 0
        consecutive_errors = 0  # 连续错误计数
        max_consecutive_errors = 10  # 最大连续错误次数（10秒）
        last_success_time = start_time
        
        while True:
            # 检查超时
            elapsed = int(time.time() - start_time)
            if elapsed >= timeout:
                logger.error(f"❌ 移动任务超时 - 任务编号: {no}, 已等待: {elapsed}s")
                # 尝试取消任务
                try:
                    self.cancel_task()
                    logger.warning("⚠️ 已尝试取消超时任务")
                except Exception as e:
                    logger.error(f"❌ 取消任务失败: {e}")
                raise TimeoutError(f"移动任务超时: 任务编号{no}, 已等待{timeout}秒")
            
            try:
                decoder = self.read_registers_function(30113, 3)
                cur_move_state = MovementState(decoder.decode_16bit_uint())
                cur_move_no = decoder.decode_32bit_int()
                
                # 读取成功，重置错误计数
                consecutive_errors = 0
                last_success_time = time.time()
                
                # 每秒记录日志，便于实时监控任务状态
                logger.info(f"⏳ 等待移动任务完成 {elapsed}s - 状态: {cur_move_state}, 任务编号: {cur_move_no}")
                
                # 检查任务编号是否匹配（如果指定了编号）
                if no != 0 and cur_move_no != no:
                    # 任务编号不匹配，可能是旧任务或新任务
                    if elapsed > 5:  # 等待5秒后如果还不匹配，记录警告
                        logger.warning(f"⚠️ 任务编号不匹配 - 期望: {no}, 实际: {cur_move_no}")
                
                # 检查暂停状态
                if cur_move_state == MovementState.MT_PAUSED:
                    logger.warning(f"⚠️ 移动任务已暂停 - 任务编号: {cur_move_no}")
                    # 可以尝试继续任务或取消
                    # self.continue_task()  # 如果需要自动继续
                
                # 检查完成状态
                if cur_move_state == MovementState.MT_FINISHED:
                    if no == 0 or cur_move_no == no:
                        decoder = self.read_registers_function(30122, 3)
                        result = MovementResult(decoder.decode_16bit_uint())
                        result_value = decoder.decode_32bit_int()
                        
                        # 检查结果是否为错误
                        if result == MovementResult.MT_TASK_ERROR:
                            error_msg = f"移动任务执行错误 - 任务编号: {no}, 错误码: {result_value}"
                            logger.error(f"❌ {error_msg}")
                            raise RuntimeError(error_msg)
                        
                        logger.info(f"✅ 移动任务完成 - 任务编号: {no if no != 0 else cur_move_no}, 结果: {result}, 耗时: {elapsed}s")
                        return [result, result_value]
                    
            except ConnectionError as e:
                consecutive_errors += 1
                error_duration = int(time.time() - last_success_time)
                
                # 如果连续错误超过阈值，认为连接长时间中断
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(f"❌ 连接长时间中断 ({error_duration}s)，已连续失败{consecutive_errors}次")
                    raise ConnectionError(
                        f"Modbus连接长时间中断: 任务编号{no}可能仍在执行，但无法继续监控。"
                        f"请检查网络连接和SSH隧道。错误: {e}"
                    )
                
                # 记录警告，但继续尝试
                logger.warning(f"⚠️ 读取失败 ({consecutive_errors}/{max_consecutive_errors}): {e}，继续尝试...")
                time.sleep(1)  # 等待1秒后重试
                continue
            
            time.sleep(1)
            i += 1

    def wait_action_task_finish(self, no=0, timeout=60):
        """
        阻塞等待任务结束，适用于动作任务
        :param no: 任务编号, 编号为0时会等待当前任务ID
        :param timeout: 超时时间（秒），默认60秒
        :return: [动作任务结果, 动作任务结果值]
        :raises TimeoutError: 任务超时
        :raises RuntimeError: 任务错误或失败
        """
        start_time = time.time()
        i = 0
        consecutive_errors = 0  # 连续错误计数
        max_consecutive_errors = 10  # 最大连续错误次数（10秒）
        last_success_time = start_time
        
        while True:
            # 检查超时
            elapsed = int(time.time() - start_time)
            if elapsed >= timeout:
                logger.error(f"❌ 动作任务超时 - 任务编号: {no}, 已等待: {elapsed}s")
                raise TimeoutError(f"动作任务超时: 任务编号{no}, 已等待{timeout}秒")
            
            try:
                decoder = self.read_registers_function(30129, 3)
                cur_action_state = ActionState(decoder.decode_16bit_uint())
                cur_action_no = decoder.decode_32bit_int()
                
                # 读取成功，重置错误计数
                consecutive_errors = 0
                last_success_time = time.time()
                
                # 每秒记录日志，便于实时监控任务状态
                logger.info(f"⏳ 等待动作任务完成 {elapsed}s - 状态: {cur_action_state}, 任务编号: {cur_action_no}")
                
                # 检查暂停状态
                if cur_action_state == ActionState.AT_PAUSED:
                    logger.warning(f"⚠️ 动作任务已暂停 - 任务编号: {cur_action_no}")
                
                if cur_action_state == ActionState.AT_FINISHED:
                    if no != 0 and cur_action_no != no:
                        time.sleep(1)
                        i += 1
                        continue
                    decoder = self.read_registers_function(30138, 3)
                    result = ActionResult(decoder.decode_16bit_uint())
                    result_value = decoder.decode_32bit_int()
                    
                    # 检查结果是否为错误
                    if result == ActionResult.AT_TASK_ERROR:
                        error_msg = f"动作任务执行错误 - 任务编号: {no}, 错误码: {result_value}"
                        logger.error(f"❌ {error_msg}")
                        raise RuntimeError(error_msg)
                    
                    logger.info(f"✅ 动作任务完成 - 任务编号: {no if no != 0 else cur_action_no}, 结果: {result}, 耗时: {elapsed}s")
                    return [result, result_value]
                
            except ConnectionError as e:
                consecutive_errors += 1
                error_duration = int(time.time() - last_success_time)
                
                # 如果连续错误超过阈值，认为连接长时间中断
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(f"❌ 连接长时间中断 ({error_duration}s)，已连续失败{consecutive_errors}次")
                    raise ConnectionError(
                        f"Modbus连接长时间中断: 任务编号{no}可能仍在执行，但无法继续监控。"
                        f"请检查网络连接和SSH隧道。错误: {e}"
                    )
                
                # 记录警告，但继续尝试
                logger.warning(f"⚠️ 读取失败 ({consecutive_errors}/{max_consecutive_errors}): {e}，继续尝试...")
                time.sleep(1)  # 等待1秒后重试
                continue
            
            time.sleep(1)
            i += 1

    def wait_locate_task_finish(self):
        """
        等待定位完成，适用于位置定位、站点定位、强制定位
        :return: 定位状态
        """
        for i in range(99):
            time.sleep(1)
            logger.info(f"⏳ 等待定位完成 {i}s")
            if self.get_cur_locate_state() == LocationState.LOCATION_STATE_RUNNING:
                logger.info("✅ 定位完成")
                return LocationState.LOCATION_STATE_RUNNING
            elif self.get_cur_locate_state() == LocationState.LOCATION_STATE_ERROR:
                logger.error("❌ 定位错误")
                return LocationState.LOCATION_STATE_ERROR

    def write_coils_function(self, address, value=True):
        """
        写入线圈
        :param address: 寄存器地址
        :param value: 取值: bool
        :return:
        """
        try:
            logger.info(f"正在写线圈: 地址={address}, 值={value}")
            builder = BinaryPayloadBuilder(byteorder=Endian.Big)
            if value:
                builder.add_16bit_uint(0xFF00)
                ret = self._client.write_coil(address, builder.to_coils(), slave=17)
            else:
                ret = self._client.write_coil(address, builder.add_16bit_uint(0x0000), slave=17)
            
            if hasattr(ret, 'isError') and ret.isError():
                logger.error(f"❌ 写线圈失败: 地址={address}, 错误={ret}")
                raise RuntimeError(f"Modbus写操作失败: {ret}")
            logger.info(f"✅ 写线圈成功: 地址={address}")
        except Exception as e:
            logger.error(f"❌ 写线圈发生异常: 地址={address}, 异常={e}")
            raise

    def pause_task(self):
        """暂停运动"""
        self.write_coils_function(1)

    def continue_task(self):
        """继续运动"""
        self.write_coils_function(2)

    def cancel_task(self):
        """停止运动"""
        self.write_coils_function(3)

    def cancel_locate_task(self):
        """停止定位"""
        self.write_coils_function(5)

    def trigger_emergency(self):
        """触发急停"""
        self.write_coils_function(7)

    def cancel_emergency(self):
        """解除急停"""
        self.write_coils_function(8)

    def charge(self):
        """启动充电"""
        self.write_coils_function(9)

    def stop_charge(self):
        """停止充电"""
        self.write_coils_function(10)

    def enter_low_power_mode(self):
        """进入低功耗模式"""
        self.write_coils_function(11)

    def exit_low_power_mode(self):
        """退出低功耗模式"""
        self.write_coils_function(12)

    def system_restart(self):
        """系统复位(重启)"""
        self.write_coils_function(14)

    def start_manual_control(self):
        """启动手动控制"""
        self.write_coils_function(15)

    def stop_manual_control(self):
        """停止手动控制"""
        self.write_coils_function(16)

    def forward_movement(self):
        """
        以0.495m/s向前移动(每设置一次移动100ms,此处是提供给开关专用,不支持速度配置,推荐使用40022-40024寄存器进行手动控制)
        :return:
        """
        self.write_coils_function(17)

    def backward_movement(self):
        """
        以0.495m/s向后移动(每设置一次移动100ms)
        :return:
        """
        self.write_coils_function(18)

    def left_rotate(self):
        """
        以0.389rad/s向左旋转(每设置一次移动100ms)
        :return:
        """
        self.write_coils_function(19)

    def right_rotate(self):
        """
        以0.389rad/s向右旋转(每设置一次移动100ms)
        :return:
        """
        self.write_coils_function(20)

    def set_DO0(self, is_high_electric):
        """
        设置高低电频
        :param is_high_electric: bool值，值为： True: 高电频
                                             False: 低电频
        :return:
        """
        self.write_coils_function(33, is_high_electric)

    def set_DO1(self, is_high_electric):
        """
        设置高低电频
        :param is_high_electric: bool值，值为： True: 高电频
                                             False: 低电频
        :return:
        """
        self.write_coils_function(34, is_high_electric)

    def set_DO2(self, is_high_electric):
        """
        设置高低电频
        :param is_high_electric: bool值，值为： True: 高电频
                                             False: 低电频
        :return:
        """
        self.write_coils_function(35, is_high_electric)

    def set_DO3(self, is_high_electric):
        """
        设置高低电频
        :param is_high_electric: bool值，值为： True: 高电频
                                             False: 低电频
        :return:
        """
        self.write_coils_function(36, is_high_electric)

    def set_DO4(self, is_high_electric):
        """
        设置高低电频
        :param is_high_electric: bool值，值为： True: 高电频
                                             False: 低电频
        :return:
        """
        self.write_coils_function(37, is_high_electric)

    def set_DO5(self, is_high_electric):
        """
        设置高低电频
        :param is_high_electric: bool值，值为： True: 高电频
                                             False: 低电频
        :return:
        """
        self.write_coils_function(38, is_high_electric)

    def set_DO6(self, is_high_electric):
        """
        设置高低电频
        :param is_high_electric: bool值，值为： True: 高电频
                                             False: 低电频
        :return:
        """
        self.write_coils_function(39, is_high_electric)

    def set_DO7(self, is_high_electric):
        """
        设置高低电频
        :param is_high_electric: bool值，值为： True: 高电频
                                             False: 低电频
        :return:
        """
        self.write_coils_function(40, is_high_electric)

    def release_action_id_131(self):
        """
        发送消息，让动作（131,0,0）结束等待。
        :return:
        """
        self.write_coils_function(49)

    def scheduling_mode(self, is_scheduling_mode):
        """
        调度模式
        :param is_scheduling_mode: bool值，值为： True: 进入调度模式
                                           False: 退出调度模式
        :return:
        """
        self.write_coils_function(51, is_scheduling_mode)

    def pause_mission_task(self):
        """暂停mission任务"""
        self.write_coils_function(97)

    def continue_mission_task(self):
        """继续mission任务"""
        self.write_coils_function(98)

    def cancel_mission_task(self):
        """取消mission任务"""
        self.write_coils_function(99)

    def read_discrete_function(self, address):
        """
        读取离散输入状态
        :param address: 寄存器地址
        :return:
        """
        for i in range(3):  # 增加重试机制
            try:
                ret = self._client.read_discrete_inputs(address, slave=17)
                if hasattr(ret, 'isError') and ret.isError():
                    logger.warning(f"⚠️ 读取离散量失败 (地址 {address}), 错误: {ret}, 正在重试 ({i+1}/3)")
                    time.sleep(0.5)
                    continue
                if not hasattr(ret, 'getBit'):
                    logger.warning(f"⚠️ 读取结果异常 (类型 {type(ret)}), 正在重试 ({i+1}/3)")
                    time.sleep(0.5)
                    continue
                val = ret.getBit(0)
                logger.debug(f"读取寄存器 {address} -> 结果: {val}")
                return val
            except Exception as e:
                logger.warning(f"⚠️ 读取离散量抛出异常 (地址 {address}): {e}, 正在重试 ({i+1}/3)")
                time.sleep(0.5)
        
        raise RuntimeError(f"❌ 无法读取离散量 (地址 {address}): Modbus 通信异常")

    def is_trigger_emergency(self) -> bool:
        """急停是否触发"""
        return self.read_discrete_function(10001)

    def is_cancel_emergency(self) -> bool:
        """急停是否可恢复"""
        return self.read_discrete_function(10002)

    def is_brake_switch(self) -> bool:
        """是否抱闸"""
        return self.read_discrete_function(10003)

    def is_charge(self) -> bool:
        """是否正在充电"""
        return self.read_discrete_function(10004)

    def is_low_power_mode(self) -> bool:
        """是否处于低功耗模式"""
        return self.read_discrete_function(10005)

    def is_obstacles_to_slow(self) -> bool:
        """是否遇到障碍物减速"""
        return self.read_discrete_function(10006)

    def is_obstacles_to_pause(self) -> bool:
        """是否遇到障碍物暂停"""
        return self.read_discrete_function(10007)

    def is_ready_for_new_movement_task(self) -> bool:
        """
        当前是否可以运行移动任务,同时满足一下条件时有效:
        系统空闲、没有急停、没有解抱闸、不是低电量模式、定位成功、
        非手动控制模式、没有移动任务或是移动任务已经结束、
        雷达、VSC、MOTOR1、MOTOR2、SRC都正常
        """
        return self.read_discrete_function(10009)

    def is_wait_release_action_id_131(self) -> bool:
        """
        是否放行,即:动作(131,0,0)会等待此信号
        :return:
        """
        return self.read_discrete_function(10049)

    def is_scheduling_mode(self) -> bool:
        """是否处于调度模式"""
        return self.read_discrete_function(10051)

    def read_registers_function(self, address, register_num, retry_count=3):
        """
        读取输入寄存器功能（带重试和自动重连）
        :param address: 寄存器地址
        :param register_num: 寄存器数量
        :param retry_count: 重试次数
        :return:
        :raises ConnectionError: 连接失败或读取失败
        """
        last_error = None
        
        for attempt in range(retry_count):
            try:
                # 检查并恢复连接
                if not self._check_and_reconnect():
                    if attempt < retry_count - 1:
                        logger.debug(f"连接检查失败，等待重试 ({attempt+1}/{retry_count})")
                        time.sleep(0.5)
                        continue
                    else:
                        raise ConnectionError(f"Modbus连接失败，无法读取寄存器: 地址{address}")
                
                ret = self._client.read_input_registers(address, count=register_num, slave=17)
                
                # 检查是否为异常响应
                if hasattr(ret, 'isError') and ret.isError():
                    error_msg = str(ret)
                    # 检查是否是连接错误
                    if "Incomplete message" in error_msg or "0 received" in error_msg:
                        logger.warning(f"⚠️ 检测到连接中断: {error_msg}")
                        if attempt < retry_count - 1:
                            # 强制重连
                            if hasattr(self._client, 'close'):
                                self._client.close()
                            time.sleep(0.5)
                            continue
                    raise ConnectionError(f"Modbus读取失败: 地址{address}, 数量{register_num}, 错误: {ret}")
                
                if not hasattr(ret, 'registers'):
                    raise ConnectionError(f"Modbus读取失败: 未返回有效数据, 响应类型: {type(ret).__name__}")
                
                decoder = BinaryPayloadDecoder.fromRegisters(ret.registers, byteorder=Endian.Big,
                                                             wordorder=Endian.Big)
                return decoder
                
            except ConnectionError as e:
                last_error = e
                if attempt < retry_count - 1:
                    logger.debug(f"读取失败，重试 ({attempt+1}/{retry_count}): {e}")
                    time.sleep(0.5)
                else:
                    raise
            except Exception as e:
                last_error = e
                error_msg = str(e)
                # 检查是否是网络相关错误
                if "Incomplete message" in error_msg or "0 received" in error_msg or "Connection" in error_msg:
                    if attempt < retry_count - 1:
                        logger.warning(f"⚠️ 网络错误，尝试重连 ({attempt+1}/{retry_count}): {e}")
                        if hasattr(self._client, 'close'):
                            self._client.close()
                        time.sleep(0.5)
                        continue
                raise ConnectionError(f"Modbus读取异常: 地址{address}, 错误: {e}")
        
        # 所有重试都失败
        raise ConnectionError(f"Modbus读取失败，已重试{retry_count}次: {last_error}")

    def read_holding_registers_function(self, address, register_num, retry_count=3):
        """
        读取保持寄存器功能（带重试和自动重连）
        :param address: 寄存器地址
        :param register_num: 寄存器数量
        :param retry_count: 重试次数
        :return:
        :raises ConnectionError: 连接失败或读取失败
        """
        last_error = None
        
        for attempt in range(retry_count):
            try:
                # 检查并恢复连接
                if not self._check_and_reconnect():
                    if attempt < retry_count - 1:
                        logger.debug(f"连接检查失败，等待重试 ({attempt+1}/{retry_count})")
                        time.sleep(0.5)
                        continue
                    else:
                        raise ConnectionError(f"Modbus连接失败，无法读取寄存器: 地址{address}")
                
                logger.debug(f"正在读取寄存器: 地址={address}, 数量={register_num}")
                ret = self._client.read_holding_registers(address, count=register_num, slave=17)
                
                # 检查是否为异常响应
                if hasattr(ret, 'isError') and ret.isError():
                    error_msg = str(ret)
                    logger.warning(f"⚠️ 读取寄存器失败: 地址={address}, 错误={error_msg}")
                    # 检查是否是连接错误
                    if "Incomplete message" in error_msg or "0 received" in error_msg:
                        logger.warning(f"⚠️ 检测到连接中断: {error_msg}")
                        if attempt < retry_count - 1:
                            # 强制重连
                            if hasattr(self._client, 'close'):
                                self._client.close()
                            time.sleep(0.5)
                            continue
                    raise ConnectionError(f"Modbus读取失败: 地址{address}, 数量{register_num}, 错误: {ret}")
                
                if not hasattr(ret, 'registers'):
                    raise ConnectionError(f"Modbus读取失败: 未返回有效数据, 响应类型: {type(ret).__name__}")
                
                decoder = BinaryPayloadDecoder.fromRegisters(ret.registers, byteorder=Endian.Big,
                                                             wordorder=Endian.Big)
                return decoder
                
            except ConnectionError as e:
                last_error = e
                if attempt < retry_count - 1:
                    logger.debug(f"读取失败，重试 ({attempt+1}/{retry_count}): {e}")
                    time.sleep(0.5)
                else:
                    raise
            except Exception as e:
                last_error = e
                error_msg = str(e)
                # 检查是否是网络相关错误
                if "Incomplete message" in error_msg or "0 received" in error_msg or "Connection" in error_msg:
                    if attempt < retry_count - 1:
                        logger.warning(f"⚠️ 网络错误，尝试重连 ({attempt+1}/{retry_count}): {e}")
                        if hasattr(self._client, 'close'):
                            self._client.close()
                        time.sleep(0.5)
                        continue
                raise ConnectionError(f"Modbus读取异常: 地址{address}, 错误: {e}")
        
        # 所有重试都失败
        raise ConnectionError(f"Modbus读取失败，已重试{retry_count}次: {last_error}")
    
    def get_cur_system_state(self) -> SystemState:
        """系统状态"""
        decoder = self.read_registers_function(30001, 1)
        dat = SystemState(decoder.decode_16bit_uint())
        return dat

    def get_cur_locate_state(self) -> LocationState:
        """定位状态"""
        decoder = self.read_registers_function(30002, 1)
        dat = LocationState(decoder.decode_16bit_uint())
        return dat

    def get_cur_pose(self) -> Pose:
        """位姿，x(毫米)、y(毫米)、yaw(弧度*1000)"""
        pose = Pose()
        decoder = self.read_registers_function(30003, 6)
        pose.x = decoder.decode_32bit_int()
        pose.y = decoder.decode_32bit_int()
        pose.yaw = decoder.decode_32bit_int()
        return pose

    def get_pose_confidence(self) -> int:
        """
        位姿置信度
        取值范围: [0,10000]，单位：0.01%
        :return:
        """
        decoder = self.read_registers_function(30009, 1)
        dat = decoder.decode_16bit_uint()
        return dat

    def get_cur_station_no(self) -> int:
        """
        当前站点编号
        :return: 返回无符号整数
        """
        decoder = self.read_registers_function(30015, 1)
        dat = decoder.decode_16bit_uint()
        return dat

    def get_operation_state(self) -> OperationState:
        """操作状态"""
        decoder = self.read_registers_function(30016, 1)
        dat = OperationState(decoder.decode_16bit_uint())
        return dat

    def get_velocity(self) -> Speed:
        """x、y方向线速度，单位mm/s，角速度，单位(1/1000)rad/s"""
        speed = Speed()
        decoder = self.read_registers_function(30017, 3)
        speed.x_dir_linear_velocity = decoder.decode_16bit_int()
        speed.y_dir_linear_velocity = decoder.decode_16bit_int()
        speed.rotate_velocity = decoder.decode_16bit_int()
        return speed

    def get_DI_state(self):
        """DI状态"""
        decoder = self.read_registers_function(30021, 1)
        dat = decoder.decode_16bit_uint()
        return dat

    def get_DO_state(self):
        """DO状态"""
        decoder = self.read_registers_function(30022, 1)
        dat = decoder.decode_16bit_uint()
        return dat

    def get_hardware_error_code(self) -> int:
        """硬件错误码"""
        decoder = self.read_registers_function(30025, 2)
        dat = decoder.decode_32bit_uint()
        return dat

    def get_last_system_error(self) -> int:
        """系统上一次错误"""
        decoder = self.read_registers_function(30027, 2)
        dat = decoder.decode_32bit_uint()
        return dat

    def get_battery_info(self) -> BatteryInfo:
        """电池状态信息"""
        battery_info = BatteryInfo()
        decoder = self.read_registers_function(30033, 8)
        battery_info.voltage = decoder.decode_16bit_uint()
        battery_info.current = decoder.decode_16bit_int()
        battery_info.temperature = decoder.decode_16bit_int()
        battery_info.remain_time = decoder.decode_16bit_uint()
        battery_info.percentage_electricity = decoder.decode_16bit_uint()
        battery_info.state = BatteryState(decoder.decode_16bit_uint())
        battery_info.use_cycles = decoder.decode_16bit_uint()
        battery_info.nominal_capacity = decoder.decode_16bit_uint()
        return battery_info

    def get_total_service(self) -> TotalService:
        """服务周期"""
        total_service = TotalService()
        decoder = self.read_registers_function(30041, 6)
        total_service.total_mileage = decoder.decode_32bit_uint()
        total_service.total_startup_time = decoder.decode_32bit_uint()
        total_service.total_startup_times = decoder.decode_32bit_uint()
        return total_service

    def get_system_cur_time(self) -> time:
        """系统当前时间，Linux时间戳"""
        decoder = self.read_registers_function(30047, 2)
        dat = decoder.decode_32bit_uint()
        return dat

    def get_communication_ip(self) -> str:
        """
        对外通信ip地址
        :return:
        """
        decoder = self.read_registers_function(30049, 4)
        ip = [decoder.decode_16bit_uint(), decoder.decode_16bit_uint(), decoder.decode_16bit_uint(),
              decoder.decode_16bit_uint()]
        dat = ".".join(str(i) for i in ip)
        return dat

    def get_system_version(self) -> str:
        """系统版本号"""
        decoder = self.read_registers_function(30053, 3)
        version = [decoder.decode_16bit_uint(), decoder.decode_16bit_uint(), decoder.decode_16bit_uint()]
        dat = ".".join(str(i) for i in version)
        return dat

    def get_pgv_scan(self) -> PGVScanDmcode:
        """下视PGV扫描到的二维码信息: 二维码ID,坐标x,y,yaw"""
        pvg_scan_dmcode = PGVScanDmcode()
        decoder = self.read_registers_function(30057, 8)
        pvg_scan_dmcode.dmcode_id = decoder.decode_32bit_int()
        pvg_scan_dmcode.x = decoder.decode_32bit_int()
        pvg_scan_dmcode.y = decoder.decode_32bit_int()
        pvg_scan_dmcode.yaw = decoder.decode_32bit_int()
        return pvg_scan_dmcode

    def get_cur_map_byte_code(self) -> int:
        """
        获取当前地图名的前两个字节编码
        比如当前地图名为:“1aa”,那么此寄存器的值为:0x3161
        """
        decoder = self.read_registers_function(30065, 1)
        dat = decoder.decode_16bit_uint()
        return dat

    def get_cur_volume(self) -> int:
        """当前系统音量"""
        decoder = self.read_registers_function(30070, 1)
        dat = decoder.decode_16bit_uint()
        return dat

    def get_hardware_error_codes(self) -> HardwareErrorCode:
        """硬件错误码1、2、3、4、5"""
        hardware_error_code = HardwareErrorCode()
        decoder = self.read_registers_function(30081, 10)
        hardware_error_code.error_code1 = decoder.decode_32bit_uint()
        hardware_error_code.error_code2 = decoder.decode_32bit_uint()
        hardware_error_code.error_code3 = decoder.decode_32bit_uint()
        hardware_error_code.error_code4 = decoder.decode_32bit_uint()
        hardware_error_code.error_code5 = decoder.decode_32bit_uint()
        return hardware_error_code

    def get_mission_task_info(self) -> MissionTask:
        """mission任务状态信息"""
        mission_task = MissionTask()
        decoder = self.read_registers_function(30097, 6)
        mission_task.mission_id = decoder.decode_32bit_uint()
        mission_task.mission_state = MissionStatus(decoder.decode_16bit_uint())
        mission_task.mission_result = MissionResult(decoder.decode_16bit_uint())
        mission_task.mission_error_code = decoder.decode_32bit_uint()
        return mission_task

    def get_movement_task_info(self) -> MovementTask:
        """移动任务状态信息"""
        movement_task = MovementTask()
        decoder = self.read_registers_function(30113, 5)
        movement_task.state = MovementState(decoder.decode_16bit_uint())
        movement_task.no = decoder.decode_32bit_int()
        movement_task.target_station = decoder.decode_16bit_uint()
        movement_task.path_no = decoder.decode_16bit_uint()
        decoder = self.read_registers_function(30122, 3)
        movement_task.result = MovementResult(decoder.decode_16bit_uint())
        movement_task.result_value = decoder.decode_32bit_uint()
        return movement_task

    def get_action_task_info(self) -> ActionTask:
        """动作任务状态信息"""
        action_task = ActionTask()
        decoder = self.read_registers_function(30129, 12)
        action_task.state = ActionState(decoder.decode_16bit_uint())
        action_task.no = decoder.decode_32bit_int()
        action_task.id = decoder.decode_32bit_int()
        action_task.param0 = decoder.decode_32bit_int()
        action_task.param1 = decoder.decode_32bit_int()
        action_task.result = ActionResult(decoder.decode_16bit_uint())
        action_task.result_value = decoder.decode_32bit_int()
        return action_task

    def pose_locate(self, x, y, angle):
        """
        位置定位
        :param x: 位姿x 单位(毫米)
        :param y: 位姿y 单位(毫米)
        :param angle: 孤度 单位(1/1000)rad
        :return:
        """
        builder = BinaryPayloadBuilder(byteorder=Endian.Big)
        builder.add_32bit_int(x)
        builder.add_32bit_int(y)
        builder.add_32bit_int(angle)
        self._client.write_registers(40001, builder.to_registers(), slave=17)

    def station_locate(self, station):
        """
        站点定位
        :param station: 站点
        :return:
        """
        builder = BinaryPayloadBuilder(byteorder=Endian.Big)
        builder.add_16bit_uint(station)
        self._client.write_registers(40007, builder.to_registers(), slave=17)

    def manual_control(self, x_speed, y_speed, yaw_speed):
        """
        手动控制，AGV设备需要开启手动模式
        每次只执行100ms，连续执行需要不断发送命令
        :param x_speed: x线速度 单位mm/s
        :param y_speed: y线速度 单位mm/s
        :param yaw_speed: w角速度 单位(1/1000)rad/s
        :return:
        """
        builder = BinaryPayloadBuilder(byteorder=Endian.Big)
        builder.add_16bit_int(x_speed)
        builder.add_16bit_int(y_speed)
        builder.add_16bit_int(yaw_speed)
        self._client.write_registers(40022, builder.to_registers(), slave=17)

    def set_speed_level(self, speed_level):
        """
        设置速度级别
        :param speed_level: 速度级别: [0,100]
        :return:
        """
        builder = BinaryPayloadBuilder(byteorder=Endian.Big)
        builder.add_16bit_uint(speed_level)
        self._client.write_registers(40026, builder.to_registers(), slave=17)

    def set_volume(self, volume):
        """
        设置扬声器音量
        :param volume: 扬声器音量: [0,100]
        :return:
        """
        builder = BinaryPayloadBuilder(byteorder=Endian.Big)
        builder.add_16bit_uint(volume)
        self._client.write_registers(40028, builder.to_registers(), slave=17)

    def switch_map(self, map_name):
        """
        切换地图，切换地图需要取消定位，因此，切换地图完成后需要重新定位
        1、根据地图名称的前两个字节设置地图
        2、若存在多张地图字符串的前两个字节相同,则按照字符排序,取第一张地图。
        3、建议用此功能时将地图名都命名为纯ASCII字符形式
        :param map_name: 地图名称
        :return:
        """
        if self.get_cur_locate_state() == LocationState.LOCATION_STATE_RUNNING:
            self.cancel_locate_task()
        builder = BinaryPayloadBuilder(byteorder=Endian.Big)
        data = 0
        map_code = bytes(map_name, 'utf-8')
        if len(map_code) >= 2:
            data = int(binascii.b2a_hex(map_code[0:2]), 16)
        elif len(map_code) == 1:
            map_code = binascii.b2a_hex(map_code)
            map_code += b'00'
            data = int(map_code, 16)
        builder.add_16bit_uint(data)
        self._client.write_registers(40029, builder.to_registers(), slave=17)

    def set_gpio_output(self, value, mask=0xFFFF):
        """
        设置GPIO output
        :param value: GPIO output的值
        :param mask: GPIO output的掩码
        :return:
        """
        builder = BinaryPayloadBuilder(byteorder=Endian.Big)
        builder.add_16bit_uint(value)
        builder.add_16bit_uint(mask)
        self._client.write_registers(40030, builder.to_registers(), slave=17)

    def mission_registers(self, mission_registers):
        """
        mission中的通用寄存器
        :param mission_registers: 寄存器实例对象
        :return:
        """
        builder = BinaryPayloadBuilder(byteorder=Endian.Big)
        builder.add_16bit_uint(mission_registers.register0)
        builder.add_16bit_uint(mission_registers.register1)
        builder.add_16bit_uint(mission_registers.register2)
        builder.add_16bit_uint(mission_registers.register3)
        builder.add_16bit_uint(mission_registers.register4)
        builder.add_16bit_uint(mission_registers.register5)
        builder.add_16bit_uint(mission_registers.register6)
        builder.add_16bit_uint(mission_registers.register7)
        self._client.write_registers(40033, builder.to_registers(), slave=17)

    def force_pose_locate(self, x, y, angle):
        """
        位置强制定位
        :param x: 位姿x 单位(毫米)
        :param y: 位姿y 单位(毫米)
        :param angle: 孤度 单位(1/1000)rad
        :return:
        """
        builder = BinaryPayloadBuilder(byteorder=Endian.Big)
        builder.add_32bit_int(x)
        builder.add_32bit_int(y)
        builder.add_32bit_int(angle)
        self._client.write_registers(40049, builder.to_registers(), slave=17)

    def move_to_pose_no(self, x, y, yaw, no=0):
        """
        自主导航移动到位置
        :param x: 单位（毫米）
        :param y:单位（毫米）
        :param yaw:单位(1/1000)rad
        :param no: 任务编号
        :return:
        """
        builder = BinaryPayloadBuilder(byteorder=Endian.Big)
        builder.add_32bit_int(no)
        builder.add_32bit_int(x)
        builder.add_32bit_int(y)
        builder.add_32bit_int(yaw)
        self._client.write_registers(
            40057, builder.to_registers(), slave=17)

    def move_to_station_no(self, station_id, no=0):
        """
        自主导航移动到站点
        :param station_id: 站点id
        :param no: 任务编号
        :return:
        """
        builder = BinaryPayloadBuilder(byteorder=Endian.Big)
        builder.add_32bit_int(no)
        builder.add_16bit_uint(station_id)
        self._client.write_registers(40066, builder.to_registers(), slave=17)

    def start_action_task_no(self, action_id, param1, param2, no=0):
        """
        执行动作任务
        :param action_id: 动作任务id
        :param param1: 参数1
        :param param2: 参数2
        :param no: 任务编号
        :return:
        """
        builder = BinaryPayloadBuilder(byteorder=Endian.Big)
        builder.add_32bit_int(no)
        builder.add_32bit_int(action_id)
        builder.add_32bit_int(param1)
        builder.add_32bit_int(param2)
        self._client.write_registers(40070, builder.to_registers(), slave=17)

    def mission_task(self, mission_id):
        """
        执行mission任务，Matrix上任务列表能看到mission对应的ID
        :param mission_id: mission任务id
        :return:
        """
        builder = BinaryPayloadBuilder(byteorder=Endian.Big)
        builder.add_32bit_uint(mission_id)
        self._client.write_registers(40097, builder.to_registers(), slave=17)

    def set_custom_funcs_comm_data(self, data):
        """
        自定义功能通信，如riot与plc数据透传通信，使用100个寄存器
        :param data: 要透传的数据内容
        :return:
        """
        assert len(data) == 100, '请一次写100个寄存器'
        self._client.write_registers(40501, data, slave=17)
    
    def get_custom_funcs_comm_data(self, reg_nb):
        """自定义功能通信，如获取riot与plc数据透传通信，使用100个寄存器"""

        assert reg_nb == 100, "num_registers 应该等于100"
        start_register = 40501
        data = []

        for i in range(reg_nb):
            decoder = self.read_holding_registers_function(start_register + i, 1)
            value = decoder.decode_16bit_int()
            data.append(value)

        return data        

if __name__ == "__main__":
    mb_server = SRModbusSdk()
    mb_server.connect_tcp("192.168.71.212")
    mb_server.is_trigger_emergency()
