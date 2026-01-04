import requests
import time

url = "http://localhost:8800/play"
params_start = {
    "file_path": "",
    "music_text": "开始播放对话",
    "play_interval": 2.0,
    "play_count": 1,
    "replay_interval": 15.0,
    "priority": 2,
    "mode": "add",
    "status_command": "start_conversation",
    "volume": 1.0
}
params_conv = {
    "file_path": "",
    "music_text": "定位出错, 请重新定位. 定位出错, 请重新定位.",
    "play_interval": 2.0,
    "play_count": 1,
    "replay_interval": 15.0,
    "priority": 2,
    "mode": "add",
    "status_command": "play_conversation",
    "volume": 1.0
}
params_stop = {
    "file_path": "",
    "music_text": "停止对话",
    "play_interval": 2.0,
    "play_count": 1,
    "replay_interval": 15.0,
    "priority": 2,
    "mode": "add",
    "status_command": "stop_conversation",
    "volume": 1.0
}
params_music = {
    "file_path": "",
    "music_text": "播放音乐音乐音乐音乐音乐音乐音乐",
    "play_interval": 2.0,
    "play_count": 1,
    "replay_interval": 15.0,
    "priority": 2,
    "mode": "add",
    "status_command": "play_music",
    "volume": 1.0
}
# 记录每个请求的响应时间
response_times = []

# 第一个请求
start_time = time.time()
response = requests.post(url, json=params_music)
end_time = time.time()
response_time = end_time - start_time
response_times.append(("播放音乐", response_time))
print(f"播放音乐响应时间: {response_time:.3f}秒")
print(response.json())  # 打印返回结果

time.sleep(1)  # 在请求之间添加间隔

# 第二个播放音乐请求
start_time = time.time()
response = requests.post(url, json=params_music)
end_time = time.time()
response_time = end_time - start_time
response_times.append(("播放音乐", response_time))
print(f"播放音乐响应时间: {response_time:.3f}秒")
print(response.json())  # 打印返回结果

# 第二个请求
start_time = time.time()

response = requests.post(url, json=params_start)
end_time = time.time()
response_time = end_time - start_time
response_times.append(("开始播放对话", response_time))
print(f"开始播放对话响应时间: {response_time:.3f}秒")
print(response.json())  # 打印返回结果

# 第三个请求
start_time = time.time()
response = requests.post(url, json=params_music)
end_time = time.time()
response_time = end_time - start_time
response_times.append(("播放音乐", response_time))
print(f"播放音乐响应时间: {response_time:.3f}秒")
print(response.json())  # 打印返回结果

# 第四个请求
start_time = time.time()
response = requests.post(url, json=params_music)
end_time = time.time()
response_time = end_time - start_time
response_times.append(("播放音乐", response_time))
print(f"播放音乐响应时间: {response_time:.3f}秒")
print(response.json())  # 打印返回结果

# 第五个请求
start_time = time.time()
response = requests.post(url, json=params_conv)
response = requests.post(url, json=params_conv)
end_time = time.time()
response_time = end_time - start_time
response_times.append(("播放对话", response_time))
print(f"播放对话响应时间: {response_time:.3f}秒")
print(response.json())  # 打印返回结果

# 第六个请求
start_time = time.time()
response = requests.post(url, json=params_stop)
end_time = time.time()
response_time = end_time - start_time
response_times.append(("停止对话", response_time))
print(f"停止对话响应时间: {response_time:.3f}秒")
# response = requests.post(url, json=params_music)
print(response.json())  # 打印返回结果
response = requests.post(url, json=params_music)
# 打印统计信息
print("\n=== 响应时间统计 ===")
total_time = sum(time for _, time in response_times)
avg_time = total_time / len(response_times)
min_time = min(time for _, time in response_times)
max_time = max(time for _, time in response_times)

print(f"总请求数: {len(response_times)}")
print(f"总响应时间: {total_time:.3f}秒")
print(f"平均响应时间: {avg_time:.3f}秒")
print(f"最快响应时间: {min_time:.3f}秒")
print(f"最慢响应时间: {max_time:.3f}秒")

print("\n=== 详细响应时间 ===")
for i, (command, response_time) in enumerate(response_times, 1):
    print(f"请求{i}: {command} - {response_time:.3f}秒")
