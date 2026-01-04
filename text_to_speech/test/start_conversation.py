import requests
import json
import time
import sys
from pathlib import Path
import logging

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 导入统一的日志配置
import log_config

# 配置日志
_logger = logging.getLogger(__name__)
url = "http://localhost:8800/play"
params_start = {
    "file_path": "Chinese/201.wav",
    "music_text": "开始播放对话",
    "play_interval": 2.0,
    "play_count": 1,
    "replay_interval": 15.0,
    "priority": 2,
    "mode": "add",
    "status_command": "start_conversation",
    "volume": 1.0
}

params_play = {
    "file_path": "",
    "music_text": "您好",
    "play_interval": 2.0,
    "play_count": 1,
    "replay_interval": 15.0,
    "priority": 2,
    "mode": "add",
    "status_command": "play_conversation",
    "volume": 1.0
}
response = requests.post(url, json=params_start)
response = requests.post(url, json=params_play)
_logger.info(response.json())
