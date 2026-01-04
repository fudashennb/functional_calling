#!/usr/bin/env python3
"""
基于VID/PID的音频设备查找函数
使用VID:ff00和PID:0002识别音频输入设备，返回sounddevice库可用的设备ID
"""

import sounddevice as sd
import subprocess

# 目标设备的VID和PID
TARGET_VID = "ff00"
TARGET_PID = "0002"

def is_target_usb_connected():
    """
    检查目标USB设备是否连接
    
    Returns:
        bool: 如果设备已连接返回True，否则返回False
    """
    try:
        result = subprocess.run(['lsusb'], capture_output=True, text=True)
        if result.returncode == 0:
            usb_devices = result.stdout.strip().split('\n')
            target_id = f"{TARGET_VID}:{TARGET_PID}"
            
            for device in usb_devices:
                if target_id in device:
                    return True
        return False
    except Exception as e:
        print(f"检查USB设备时出错: {e}")
        return False

def get_audio_device_id():
    """
    获取目标音频设备的设备ID
    
    Returns:
        int: 设备ID，如果目标设备未连接则返回默认输入设备ID
    """
    try:
        # 首先检查目标USB设备是否连接
        if not is_target_usb_connected():
            print(f"目标设备 (VID:{TARGET_VID}, PID:{TARGET_PID}) 未连接")
            # 返回默认输入设备
            default_input = sd.query_devices(kind='input')
            return default_input['index'] if default_input else 0
        
        print(f"目标音频输入设备 (VID:{TARGET_VID}, PID:{TARGET_PID}) 已连接")
        
        # 目标设备已连接，查找DOV USB Audio设备
        devices = sd.query_devices()
        for i, device in enumerate(devices):
            device_name = device.get('name', '')
            # 查找包含"DOV"的设备，不检查输入通道数
            if 'DOV' in device_name:
                # print(f"找到DOV USB Audio设备: {device_name} (ID: {i})")
                return i
        
        # 如果没有找到DOV设备，使用默认输入设备
        print("未找到DOV USB Audio设备，使用默认输入设备")
        default_input = sd.query_devices(kind='input')
        if default_input:
            print(f"使用默认输入设备: {default_input['name']} (ID: {default_input['index']})")
            return default_input['index']
        
        # 备选方案：查找第一个可用的输入设备
        for i, device in enumerate(devices):
            if device.get('max_input_channels', 0) > 0:
                print(f"使用备选输入设备: {device['name']} (ID: {i})")
                return i
        
        # 最后的备选方案
        return 0
        
    except Exception as e:
        print(f"获取音频设备ID时出错: {e}")
        # 出错时返回默认输入设备
        try:
            default_input = sd.query_devices(kind='input')
            return default_input['index'] if default_input else 0
        except:
            return 0

def get_audio_device_name():
    """
    获取音频设备的设备名称
    
    Returns:
        str: 设备名称
    """
    device_id = get_audio_device_id()
    try:
        devices = sd.query_devices()
        if device_id < len(devices):
            return devices[device_id]['name']
    except:
        pass
    return "default"

def get_device_info():
    """
    获取设备的详细信息
    
    Returns:
        dict: 包含VID、PID、连接状态、设备ID、名称等信息
    """
    info = {
        'vid': TARGET_VID,
        'pid': TARGET_PID,
        'connected': is_target_usb_connected(),
        'device_id': None,
        'device_name': None,
        'max_input_channels': 0,
        'default_samplerate': 0
    }
    
    if info['connected']:
        device_id = get_audio_device_id()
        info['device_id'] = device_id
        info['device_name'] = get_audio_device_name()
        
        try:
            devices = sd.query_devices()
            if device_id < len(devices):
                device = devices[device_id]
                info['max_input_channels'] = device.get('max_input_channels', 0)
                info['default_samplerate'] = device.get('default_samplerate', 0)
        except:
            pass
    
    return info

# 使用示例
if __name__ == "__main__":
    print("=== 基于VID/PID的音频设备查找 ===")
    print(f"目标设备: VID:{TARGET_VID}, PID:{TARGET_PID}")
    
    # 获取设备信息
    device_info = get_device_info()
    
    print(f"设备连接状态: {'已连接' if device_info['connected'] else '未连接'}")
    
    if device_info['connected']:
        print(f"设备ID: {device_info['device_id']}")
        print(f"设备名称: {device_info['device_name']}")
        print(f"输入通道数: {device_info['max_input_channels']}")
        print(f"默认采样率: {device_info['default_samplerate']}")
    else:
        print("设备未连接，将使用默认输入设备")
        device_id = get_audio_device_id()
        device_name = get_audio_device_name()
        print(f"默认设备ID: {device_id}")
        print(f"默认设备名称: {device_name}") 