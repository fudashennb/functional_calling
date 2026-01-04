#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
创建测试音频文件
"""

import wave
import numpy as np
import os

def create_test_wav_file(filename, duration_seconds=5.0, sample_rate=22050):
    """创建一个测试WAV文件"""
    try:
        # 生成音频数据（简单的正弦波）
        t = np.linspace(0, duration_seconds, int(sample_rate * duration_seconds), False)
        # 生成440Hz的正弦波
        audio_data = 0.3 * np.sin(2 * np.pi * 440 * t)
        
        # 转换为16位整数
        audio_int16 = (audio_data * 32767).astype(np.int16)
        
        # 创建WAV文件
        with wave.open(filename, 'w') as wav_file:
            wav_file.setnchannels(1)  # 单声道
            wav_file.setsampwidth(2)  # 16位
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio_int16.tobytes())
        
        print(f"✅ 测试音频文件已创建: {filename}")
        print(f"   时长: {duration_seconds} 秒")
        print(f"   采样率: {sample_rate} Hz")
        print(f"   文件大小: {len(audio_int16) * 2} 字节")
        
        return True
        
    except Exception as e:
        print(f"❌ 创建测试音频文件失败: {e}")
        return False

def main():
    """主函数"""
    print("创建测试音频文件...")
    
    # 创建/tmp目录（如果不存在）
    os.makedirs("/tmp", exist_ok=True)
    
    # 创建不同时长的测试文件
    test_files = [
        ("/tmp/test_audio_3s.wav", 3.0),
        ("/tmp/test_audio_5s.wav", 5.0),
        ("/tmp/test_audio_10s.wav", 10.0),
    ]
    
    for filename, duration in test_files:
        create_test_wav_file(filename, duration)
        print()
    
    print("测试音频文件创建完成！")
    print("现在可以运行 test_duration.py 来测试音频文件时长计算功能。")

if __name__ == "__main__":
    main() 