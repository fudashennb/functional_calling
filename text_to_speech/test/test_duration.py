#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试预估时间功能
"""

import requests
import json
import time

# 服务器地址
BASE_URL = "http://localhost:8800"

def test_audio_file_duration():
    """测试音频文件时长计算"""
    print("=== 测试音频文件时长计算 ===")
    
    # 测试不同时长的音频文件
    test_files = [
        "/tmp/test_audio_3s.wav",
        "/tmp/test_audio_5s.wav", 
        "/tmp/test_audio_10s.wav"
    ]
    
    for i, file_path in enumerate(test_files, 1):
        print(f"\n测试音频文件 {i}: {file_path}")
        
        # 模拟播放音频文件的请求
        payload = {
            "file_path": file_path,
            "play_count": 2,
            "play_interval": 1.0,
            "status_command": "play_music",
            "mode": "add"
        }
        
        try:
            response = requests.post(f"{BASE_URL}/play", json=payload)
            print(f"状态码: {response.status_code}")
            result = response.json()
            print(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
            
            # 检查duration字段
            if 'task' in result and 'duration' in result['task']:
                print(f"✅ 任务duration字段: {result['task']['duration']} 秒")
            else:
                print("❌ 缺少duration字段")
                
        except Exception as e:
            print(f"请求失败: {e}")

def test_text_duration():
    """测试文本播放时长估算"""
    print("\n=== 测试文本播放时长估算 ===")
    
    # 测试不同长度的文本
    test_texts = [
        "你好世界",
        "Hello World",
        "这是一个测试文本，包含中英文混合内容。This is a test text with mixed Chinese and English content.",
        "很长的文本测试，包含很多字符。这是一个用于测试预估时间功能的文本，应该能够正确估算出播放时长。The quick brown fox jumps over the lazy dog. 1234567890"
    ]
    
    for i, text in enumerate(test_texts, 1):
        print(f"\n测试文本 {i}: {text}")
        
        payload = {
            "music_text": text,
            "play_count": 2,
            "play_interval": 1.5,
            "status_command": "play_text",
            "mode": "add"
        }
        
        try:
            response = requests.post(f"{BASE_URL}/play", json=payload)
            print(f"状态码: {response.status_code}")
            result = response.json()
            print(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
            
            # 检查duration字段
            if 'task' in result and 'duration' in result['task']:
                print(f"✅ 任务duration字段: {result['task']['duration']} 秒")
            else:
                print("❌ 缺少duration字段")
                
        except Exception as e:
            print(f"请求失败: {e}")

def test_status_with_duration():
    """测试状态API中的时长信息"""
    print("\n=== 测试状态API中的时长信息 ===")
    
    try:
        response = requests.get(f"{BASE_URL}/status")
        print(f"状态码: {response.status_code}")
        result = response.json()
        print(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
    except Exception as e:
        print(f"请求失败: {e}")

def test_mixed_queue():
    """测试混合队列的时长计算"""
    print("\n=== 测试混合队列的时长计算 ===")
    
    # 添加多个不同类型的任务到队列
    tasks = [
        {
            "music_text": "第一个文本任务",
            "play_count": 1,
            "status_command": "play_text",
            "mode": "add"
        },
        {
            "music_text": "第二个文本任务，包含更多内容",
            "play_count": 2,
            "play_interval": 1.0,
            "status_command": "play_text",
            "mode": "add"
        },
        {
            "file_path": "/tmp/test1.wav",
            "play_count": 1,
            "status_command": "play_music",
            "mode": "add"
        }
    ]
    
    for i, task in enumerate(tasks, 1):
        print(f"\n添加任务 {i}: {task.get('music_text', task.get('file_path', 'Unknown'))}")
        try:
            response = requests.post(f"{BASE_URL}/play", json=task)
            print(f"状态码: {response.status_code}")
            result = response.json()
            print(f"预估时长: {result.get('estimated_duration', 'N/A')}")
        except Exception as e:
            print(f"请求失败: {e}")
    
    # 查看队列状态
    print("\n查看队列状态:")
    test_status_with_duration()

if __name__ == "__main__":
    print("开始测试预估时间功能...")
    
    # 等待服务器启动
    print("等待服务器启动...")
    time.sleep(2)
    
    # 运行测试
    test_audio_file_duration()
    test_text_duration()
    test_status_with_duration()
    test_mixed_queue()
    
    print("\n测试完成！") 