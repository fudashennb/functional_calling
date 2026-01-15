# Copyright 2025 Standard Robots Co. All rights reserved.
import log_config
from enum import Enum
import sounddevice as sd
import numpy as np
from scipy.io import wavfile
import time
from collections import deque
import requests
import os
from typing import Dict, List, Any, Optional, Tuple
import threading
import queue
import logging
import sqlite3
from pathlib import Path
import sys
import subprocess
import tempfile
import csv
import datetime
# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 导入统一的日志配置
import log_config
# 配置日志
_logger = logging.getLogger(__name__)

# 导入配置驱动的文本处理器
from config.text_processor import get_text_processor
from config.config_loader import get_config_loader

# 自定义ALSA音频流类


class ALSAStreamReader:
    """使用ALSA直接读取音频流的类"""

    def __init__(self, device_name="hw:0,0", sample_rate=48000, channels=2, chunk_size=1440):
        self.device_name = device_name
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self.process = None
        self.is_running = False

    def start(self):
        """启动ALSA音频流"""
        try:
            # 使用arecord命令直接读取ALSA设备
            cmd = [
                'arecord',
                '-D', self.device_name,
                '-r', str(self.sample_rate),
                '-c', str(self.channels),
                '-f', 'S16_LE',
                '-t', 'raw'
            ]

            _logger.info(f"🎤 启动ALSA音频流: {' '.join(cmd)}")
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0
            )
            self.is_running = True
            _logger.info("✅ ALSA音频流启动成功")
            return True

        except Exception as e:
            _logger.error(f"❌ 启动ALSA音频流失败: {e}")
            return False

    def read_chunk(self):
        """读取一个音频chunk"""
        if not self.is_running or not self.process:
            return None

        try:
            # 读取原始音频数据
            raw_data = self.process.stdout.read(
                self.chunk_size * self.channels * 2)  # 16位 = 2字节
            if not raw_data:
                return None

            # 转换为numpy数组
            audio_data = np.frombuffer(raw_data, dtype=np.int16)

            # 重塑为正确的形状
            if self.channels == 2:
                audio_data = audio_data.reshape(-1, 2)

            # 转换为float32并归一化
            audio_float = audio_data.astype(np.float32) / 32767.0

            return audio_float

        except Exception as e:
            _logger.error(f"❌ 读取音频chunk失败: {e}")
            return None

    def stop(self):
        """停止ALSA音频流"""
        self.is_running = False
        if self.process:
            self.process.terminate()
            self.process.wait()
            self.process = None
        _logger.info("🛑 ALSA音频流已停止")


# 导入设备查找函数
try:
    from dov_device_finder import get_audio_device_id, get_audio_device_name, is_target_usb_connected
    _logger.info("✅ 成功导入设备查找函数")
except ImportError as e:
    _logger.error(f"❌ 导入设备查找函数失败: {e}")
    # 定义备用函数

    def get_audio_device_id():
        try:
            import sounddevice as sd
            default_input = sd.query_devices(kind='input')
            return default_input['index'] if default_input else 0
        except:
            return 0

    def get_audio_device_name():
        return "default"

    def is_target_usb_connected():
        return False

# ============================================================================
# 对话状态枚举
# ============================================================================


class ConversationState(Enum):
    WAIT_FOR_WAKEUP = 1
    IN_BUTTON_CONVERSATION = 2
    IN_WAKEUP_CONVERSATION = 3


# ============================================================================
# 工具函数部分 (重用已有函数)
# ============================================================================
def get_para_value(key: str):
    try:
        DB_PATH = '/sros/db/main.db3'
        db = sqlite3.connect(DB_PATH)
        cursor = db.cursor()
        cmd = f'SELECT value FROM func_config where key = "{key}" and is_valid=1;'
        cursor.execute(cmd)
        value = cursor.fetchone()[0]
        return value
    except Exception as e:
        _logger.error(f"get_para_value error: {e}")
        return None

def save_result_to_csv(result: dict, csv_file_path: str = "asr_results.csv"):
    """保存result到CSV文件"""
    try:
        file_exists = os.path.exists(csv_file_path)
        row = {"时间戳": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **result}
        with open(csv_file_path, 'a', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
    except Exception as e:
        _logger.error(f"保存CSV失败: {e}")


def get_text_from_server(audio_file_path, hot_words="", server_url="http://10.10.20.30:5000/recognize"):
    """
    从ASR服务器获取语音识别结果

    参数:
        audio_file_path: 音频文件路径
        hot_words: 热词（可选）
        server_url: 服务器URL

    返回:
        dict: 包含识别结果的字典
    """
    try:
        # 检查文件是否存在
        if not os.path.exists(audio_file_path):
            return {
                "success": False,
                "error": f"音频文件不存在: {audio_file_path}",
                "text": ""
            }

        # 检查文件格式
        allowed_extensions = {'.wav', '.mp3', '.flac', '.m4a', '.ogg'}
        file_ext = os.path.splitext(audio_file_path)[1].lower()
        if file_ext not in allowed_extensions:
            return {
                "success": False,
                "error": f"不支持的文件格式: {file_ext}，支持的格式: {', '.join(allowed_extensions)}",
                "text": ""
            }

        _logger.info(f"正在发送音频文件到ASR服务器: {audio_file_path}")

        # 准备文件和表单数据
        files = {
            'audio': (os.path.basename(audio_file_path), open(audio_file_path, 'rb'))
        }

        data = {}
        if hot_words:
            data['hot_words'] = hot_words
            _logger.info(f"使用热词: {hot_words}")

        # 发送POST请求
        response = requests.post(
            server_url,
            files=files,
            data=data,
            timeout=30  # 30秒超时
        )

        # 关闭文件
        files['audio'][1].close()

        # 检查HTTP状态码
        if response.status_code == 200:
            result = response.json()
            _logger.info(f"服务器响应: {result}")
            # save_result_to_csv(result)
            return result
        else:
            error_msg = f"服务器返回错误状态码: {response.status_code}"
            try:
                error_detail = response.json()
                error_msg += f", 详情: {error_detail}"
            except:
                error_msg += f", 响应内容: {response.text}"

            return {
                "success": False,
                "error": error_msg,
                "text": ""
            }

    except requests.exceptions.Timeout:
        return {
            "success": False,
            "error": "请求超时，服务器响应时间过长",
            "text": ""
        }
    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "error": f"无法连接到服务器: {server_url}",
            "text": ""
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"请求失败: {str(e)}",
            "text": ""
        }

# ============================================================================
# 核心功能类 (单一职责原则)
# ============================================================================


class VoiceActivityDetector:
    """语音活动检测器 - 单一职责：检测语音活动"""

    def __init__(self,
                 sample_rate: int = 16000,
                 frame_duration_ms: int = 30,
                 energy_threshold: float = 0.003,  # 降低默认阈值
                 silence_duration_ms: int = 1500,
                 min_speech_duration_ms: int = 300,  # 降低最小语音时长
                 speech_detection_threshold_ms: int = 150):  # 添加150ms语音检测阈值
        """
        初始化语音活动检测器

        Args:
            sample_rate: 采样率
            frame_duration_ms: 帧长度（毫秒）
            energy_threshold: 能量阈值，低于此值认为是静音
            silence_duration_ms: 静音持续时间阈值（毫秒），超过此时间认为说话结束
            min_speech_duration_ms: 最小语音时长（毫秒），避免误触发
            speech_detection_threshold_ms: 语音检测阈值（毫秒），只有超过此时长才认为检测到语音
        """
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.energy_threshold = energy_threshold
        self.silence_duration_ms = silence_duration_ms
        self.min_speech_duration_ms = min_speech_duration_ms
        self.speech_detection_threshold_ms = speech_detection_threshold_ms

        # 计算帧大小
        self.frame_size = int(sample_rate * frame_duration_ms / 1000)
        self.silence_frames_threshold = int(
            silence_duration_ms / frame_duration_ms)
        self.min_speech_frames = int(
            min_speech_duration_ms / frame_duration_ms)
        self.speech_detection_frames_threshold = int(
            speech_detection_threshold_ms / frame_duration_ms)

        # 状态变量
        self.silence_frame_count = 0
        self.speech_frame_count = 0
        self.total_speech_duration_ms = 0  # 累计语音时长

    def calculate_frame_energy(self, frame: np.ndarray) -> float:
        """计算音频帧的能量"""
        if len(frame) == 0:
            return 0.0
        # 计算RMS能量
        return np.sqrt(np.mean(frame ** 2))

    def detect_speech_start(self, frame: np.ndarray) -> bool:
        """
        检测语音开始

        Args:
            frame: 音频帧数据

        Returns:
            bool: 是否检测到语音开始
        """
        # 计算帧能量
        energy = self.calculate_frame_energy(frame)
        # 判断当前帧是否有语音
        has_voice = energy > self.energy_threshold

        if has_voice:
            self.silence_frame_count = 0
            self.speech_frame_count += 1
            self.total_speech_duration_ms += self.frame_duration_ms
            # 只有累计语音时长超过阈值，才会设置speech_detected为True
            if self.total_speech_duration_ms >= self.speech_detection_threshold_ms:
                _logger.info(
                    f"✅ 确认检测到语音 (累计时长: {self.total_speech_duration_ms}ms >= {self.speech_detection_threshold_ms}ms)")
                return True
        else:
            self.silence_frame_count += 1
            if self.silence_frame_count >= 2:
                self.total_speech_duration_ms = 0
        return False

    def detect_speech_end(self, frame: np.ndarray) -> bool:
        """
        检测语音结束

        Args:
            frame: 音频帧数据

        Returns:
            bool: 是否检测到语音结束
        """
        # 计算帧能量
        energy = self.calculate_frame_energy(frame)

        # 判断当前帧是否有语音
        has_voice = energy > self.energy_threshold

        if has_voice:
            # 当前帧有语音，重置静音计数
            self.silence_frame_count = 0
            self.speech_frame_count += 1
            self.total_speech_duration_ms += self.frame_duration_ms
            return False

        else:
            # 当前帧为静音
            self.silence_frame_count += 1
            # 检查是否达到静音阈值
            if self.silence_frame_count >= self.silence_frames_threshold:
                return True

        return False

    def reset(self):
        """重置检测器状态"""
        self.silence_frame_count = 0
        self.speech_frame_count = 0
        self.total_speech_duration_ms = 0  # 重置累计语音时长


class AudioRecorder:
    """音频录制器 - 单一职责：录制音频"""

    def __init__(self, sample_rate=16000, channels=2):
        self.sample_rate = sample_rate
        self.channels = channels

    def record(self, duration: float) -> Optional[str]:
        """
        录制音频并保存为临时文件

        Args:
            duration: 录制时长（秒）

        Returns:
            str: 临时文件路径，失败返回None
        """
        try:
            _logger.info(f"🎙️ 开始录制音频，时长: {duration}秒...")
            _logger.info("🔴 录音中...")

            # 录制音频
            audio_data = sd.rec(
                int(duration * self.sample_rate),
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=np.float32
            )
            sd.wait()  # 等待录音完成

            _logger.info("⏹️ 录音完成")

            # 生成临时文件名
            timestamp = int(time.time())
            temp_file = f"temp_recording_{timestamp}.wav"

            # 转换为16位整数并保存
            audio_int16 = (audio_data * 32767).astype(np.int16)
            wavfile.write(temp_file, self.sample_rate, audio_int16)

            _logger.info(f"💾 音频已保存到: {temp_file}")
            _logger.info(f"   时长: {len(audio_data) / self.sample_rate:.2f}秒")
            _logger.info(f"   采样率: {self.sample_rate}Hz")
            _logger.info(f"   通道数: {self.channels}")

            return temp_file

        except Exception as e:
            _logger.error(f"❌ 录制音频失败: {e}")
            return None


class SpeechRecognizer:
    """语音识别器 - 单一职责：语音识别"""

    def __init__(self):
        pass

    def recognize(self, audio_file_path: str, server_url: str, hot_words: str = "") -> Optional[str]:
        """
        识别语音文件中的文本

        Args:
            audio_file_path: 音频文件路径
            hot_words: 热词

        Returns:
            str: 识别的文本，失败返回None
        """
        try:
            _logger.info(f"🔄 正在进行语音识别...")
            _logger.info(f"   音频文件: {audio_file_path}")
            _logger.info(f"   ASR服务器: {server_url}")

            # 使用已有的ASR函数
            result = get_text_from_server(
                audio_file_path, hot_words, server_url)

            if result["success"]:
                recognized_text = result["text"].strip()
                _logger.info(f"✅ 语音识别成功: '{recognized_text}'")
                return recognized_text
            else:
                _logger.error(f"❌ 语音识别失败: {result['error']}")
            return None

        except Exception as e:
            _logger.error(f"❌ 语音识别过程中出错: {e}")
            return None


class TextProcessor:
    """文本处理器 - 单一职责：文本处理"""

    @staticmethod
    def process_punctuation(text: str) -> str:
        """
        处理文本标点符号，将标点符号替换为". "

        Args:
            text: 原始文本

        Returns:
            str: 处理后的文本
        """
        if not text:
            return text

        try:
            _logger.info(f"📝 处理文本标点符号...")
            _logger.info(f"   原始文本: '{text}'")

            # 定义需要替换的标点符号
            punctuation_marks = ['，', '。', '！', '？', '；',
                                 '：', ',', '.', '!', '?', ';', ':', '、']

            processed_text = text
            for punct in punctuation_marks:
                processed_text = processed_text.replace(punct, '. ')

            # 清理多余的空格和点号
            processed_text = ' '.join(processed_text.split())  # 清理多余空格
            processed_text = processed_text.replace('. . ', '. ')  # 清理重复的点号
            processed_text = processed_text.replace('..', '.')  # 清理连续的点号

            # 确保文本以句号结尾
            if processed_text and not processed_text.endswith('.'):
                processed_text += '.'

            _logger.info(f"   处理后文本: '{processed_text}'")
            return processed_text

        except Exception as e:
            _logger.error(f"❌ 处理文本时出错: {e}")
            return text

# ============================================================================
# 主要协调器类
# ============================================================================


class TTSData:
    def __init__(self, volume: float = 0.0):
        self.volume = 0.55
        self.tts_url = "http://localhost:8800/play"
        self.get_conversation_playing_status_url = "http://localhost:8800/get_conversation_playing_status"
        self.conversation_active = False  # 对话状态标志

    def set_volume(self, volume: float):
        self.volume = volume
        _logger.info(f"set volume to {volume}")

    def start_conversation(self):
        """
        开始对话模式

        Returns:
            bool: 是否成功启动对话模式
        """
        try:
            _logger.info("🎤 正在启动对话模式...")

            # 构建请求数据
            request_data = {
                "status_command": "start_conversation",
                "volume": self.volume
            }

            # 发送HTTP请求到TTS服务器
            response = requests.post(
                self.tts_url,
                json=request_data,
                timeout=10
            )

            if response.status_code == 200:
                result = response.json()
                if result.get('status') == 'success':
                    self.conversation_active = True
                    # Added: Wait for server confirmation
                    status = {}
                    confirmed = False  # Added: Flag
                    for _ in range(1):  # Modified: Minimized to 1 for max 0.03s wait
                        status = self.get_status()
                        _logger.info(f"模式确认轮询: {status}")
                        if status.get('current_mode') == 'conversation':
                            confirmed = True
                            break  # Break on confirmation
                        time.sleep(0.03)  # Sleep 0.03s
                    if not confirmed:
                        # Modified: Warn and continue
                        _logger.warning("⚠️ 模式切换未完全确认，但继续执行")
                        # No return False
                    _logger.info("✅ 对话模式启动成功")
                    _logger.info(
                        f"   当前模式: {status.get('current_mode', 'conversation')}")
                    # Added: Notify external systems to pause music requests
                    try:
                        requests.post(
                            'http://localhost:8800/pause_music', timeout=1)
                        _logger.info("✅ 已通知外部系统暂停音乐请求")
                    except Exception as e:
                        _logger.warning(f"⚠️ 通知失败: {e}")
                    return True
                else:
                    _logger.error(
                        f"❌ 启动对话模式失败: {result.get('message', '未知错误')}")
                    return False
            else:
                _logger.error(f"❌ HTTP请求失败: {response.status_code}")
                return False

        except requests.exceptions.ConnectionError:
            _logger.error(f"❌ 无法连接到TTS服务器: {self.tts_url}")
            return False
        except requests.exceptions.Timeout:
            _logger.error("❌ 请求超时")
            return False
        except Exception as e:
            _logger.error(f"❌ 启动对话模式时出错: {e}")
            return False

    def stop_conversation(self):
        """
        停止对话模式

        Returns:
            bool: 是否成功停止对话模式
        """
        try:
            _logger.info("🛑 正在停止对话模式...")

            # 构建请求数据
            request_data = {
                "status_command": "stop_conversation",
                "volume": self.volume
            }

            # 发送HTTP请求到TTS服务器
            response = requests.post(
                self.tts_url,
                json=request_data,
                timeout=10
            )

            if response.status_code == 200:
                result = response.json()
                if result.get('status') == 'success':
                    self.conversation_active = False
                    _logger.info("✅ 对话模式已停止")
                    _logger.info(
                        f"   当前模式: {result.get('current_mode', 'music')}")
                    return True
                else:
                    _logger.error(
                        f"❌ 停止对话模式失败: {result.get('message', '未知错误')}")
                    return False
            else:
                _logger.error(f"❌ HTTP请求失败: {response.status_code}")
                return False

        except requests.exceptions.ConnectionError:
            _logger.error(f"❌ 无法连接到TTS服务器: {self.tts_url}")
            return False
        except requests.exceptions.Timeout:
            _logger.error("❌ 请求超时")
            return False
        except Exception as e:
            _logger.error(f"❌ 停止对话模式时出错: {e}")
            return False

    def play_conversation(self, text: str, use_path: bool = False):
        """
        播放对话内容

        Args:
            text: 要播放的文本内容

        Returns:
            bool: 是否成功播放
        """
        try:
            if not text or not text.strip():
                _logger.error("❌ 文本内容为空，无法播放")
                return False

            _logger.info(f"🔊 正在播放对话内容: '{text}'")

            # 构建请求数据
            request_data = {
                "status_command": "play_conversation",
                "music_text": text,
                "volume": self.volume,
                "play_count": 1,
                "play_interval": 1.0,
                "priority": 1,
                "mode": "replace"
            }
            if use_path:
                request_data["file_path"] = text
                request_data["music_text"] = ""
            # 发送HTTP请求到TTS服务器
            response = requests.post(
                self.tts_url,
                json=request_data,
                timeout=10
            )

            if response.status_code == 200:
                result = response.json()
                if result.get('status') == 'success':
                    task_info = result.get('task', {})
                    _logger.info("✅ 对话内容播放任务已添加")
                    _logger.info(
                        f"   播放模式: {result.get('current_mode', 'conversation')}")
                    _logger.info(
                        f"   预估时长: {task_info.get('duration', 0):.2f}秒")
                    return response
                else:
                    _logger.error(
                        f"❌ 播放对话内容失败: {result.get('message', '未知错误')}")
                    return None
            else:
                _logger.error(f"❌ HTTP请求失败: {response.status_code}")
                try:
                    error_detail = response.json()
                    _logger.error(f"   错误详情: {error_detail}")
                except:
                    _logger.error(f"   响应内容: {response.text}")
                return None

        except requests.exceptions.ConnectionError:
            _logger.error(f"❌ 无法连接到TTS服务器: {self.tts_url}")
            return None
        except requests.exceptions.Timeout:
            _logger.error("❌ 请求超时")
            return None
        except Exception as e:
            _logger.error(f"❌ 播放对话内容时出错: {e}")
            return None

    def get_status(self):
        """
        获取当前系统状态

        Returns:
            dict: 系统状态信息
        """
        try:
            response = requests.get(
                f"{self.tts_url.replace('/play', '/status')}",
                timeout=10
            )

            if response.status_code == 200:
                result = response.json()
                if result.get('status') == 'success':
                    return result.get('data', {})
                else:
                    _logger.error(f"❌ 获取状态失败: {result.get('message', '未知错误')}")
                    return {}
            else:
                _logger.error(f"❌ HTTP请求失败: {response.status_code}")
                return {}

        except requests.exceptions.ConnectionError:
            _logger.error(f"❌ 无法连接到TTS服务器: {self.tts_url}")
            return {}
        except requests.exceptions.Timeout:
            _logger.error("❌ 请求超时")
            return {}
        except Exception as e:
            _logger.error(f"❌ 获取状态时出错: {e}")
            return {}

    def is_conversation_active(self):
        """
        检查对话模式是否激活

        Returns:
            bool: 对话模式是否激活
        """
        return self.conversation_active

    def get_conversation_playing_status(self):
        try:
            response = requests.get(
                self.get_conversation_playing_status_url,
                timeout=1
            )
            response_data = response.json()
            # 提取返回内容为字典形式
            return {
                'is_playing_now': response_data.get('is_playing_now', True),
                'last_play_end_time': response_data.get('last_play_end_time', None)
            }
        except Exception as e:
            _logger.error(f"❌ 获取对话播放状态时出错: {e}")
            return {
                'is_playing_now': False,
                'last_play_end_time': None
            }


class AudioStreamReader:
    """音频流读取器 - 协调器模式，组合各个功能类"""
    class AudioData:
        def __init__(self, audio_data: np.ndarray, vad_type: str, vad_duration: float, vad_start_time: float, vad_end_time: float):
            self.audio_data = audio_data
            self.vad_type = vad_type
            self.vad_duration = vad_duration
            self.vad_start_time = vad_start_time
            self.vad_end_time = vad_end_time

    def __init__(self,
                 asr_server_url: str = "http://10.10.20.30:5000/recognize",
                 dialog_ai_url: str = "http://localhost:8766/v1/voice/query",  # 改为 functional_call 新服务端
                 sample_rate: int = 48000,  # 修改为48000Hz以匹配硬件支持
                 channels: int = 1,  # 修改为单通道，避免通道数问题
                 vad_queue_size: int = 5000,  # 减少队列大小，保持轻量
                 vad_min_chunks: int = 30,  # 调整为30个chunk，确保有足够数据检测静音
                 max_speech_duration: float = 30.0,  # 最大语音时长（秒）
                 max_processing_time: float = 35.0,
                 nick_name: str = "1307"):  # 最大处理时间（秒）
        """
        初始化音频流读取器

        Args:
            asr_server_url: ASR服务器地址
            sample_rate: 采样率
            channels: 通道数
            vad_queue_size: VAD队列最大容量（约15秒音频数据，保持轻量）
            vad_min_chunks: VAD处理所需的最小chunk数量（30个chunk，约900ms，确保静音检测）
            max_speech_duration: 最大语音时长（秒，超时强制结束）
            max_processing_time: 最大处理时间（秒，防止无限等待）
        """
        _logger.info("🔧 初始化音频流读取器...")

        # 配置参数
        self.asr_server_url = asr_server_url
        self.dialog_ai_url = dialog_ai_url
        self.sample_rate = sample_rate
        self.channels = channels

        # 获取音频设备ID
        try:
            self.device_id = get_audio_device_id()
            _logger.info(f"🎤 获取到音频设备ID: {self.device_id}")

            # 检查设备是否支持输入
            devices = sd.query_devices()
            if self.device_id < len(devices):
                device = devices[self.device_id]
                max_input_channels = device.get('max_input_channels', 0)
                _logger.info(f"   设备名称: {device.get('name', 'unknown')}")
                _logger.info(f"   最大输入通道数: {max_input_channels}")

                # 如果设备不支持输入，但设备名称包含DOV，使用ALSA流读取器
                if max_input_channels == 0 and 'DOV' in device.get('name', ''):
                    _logger.warning(
                        f"⚠️ 设备 {self.device_id} 的max_input_channels为0，但ALSA显示有输入功能")
                    _logger.info(f"   将使用ALSA直接读取音频流")
                    self.use_alsa_stream = True
                    self.alsa_stream = ALSAStreamReader(
                        device_name="hw:0,0",
                        sample_rate=self.sample_rate,
                        channels=self.channels,
                        chunk_size=int(self.sample_rate * 0.03)  # 30ms chunks
                    )
                elif max_input_channels == 0:
                    _logger.error(f"❌ 设备 {self.device_id} 不支持音频输入")
                    self.device_id = None
                    self.use_alsa_stream = False
                else:
                    self.use_alsa_stream = False
            else:
                self.use_alsa_stream = False
        except Exception as e:
            _logger.warning(f"⚠️ 获取音频设备ID失败: {e}，使用默认设备")
            self.device_id = None
            self.use_alsa_stream = False

        # VAD相关参数
        self.vad_queue_size = vad_queue_size
        self.vad_min_chunks = vad_min_chunks
        self.max_speech_duration = max_speech_duration  # 最大语音时长
        self.max_processing_time = max_processing_time  # 最大处理时间
        self.silence_duration_ms = 1300  # 静音检测阈值，降低为300ms

        # 初始化各个功能组件
        self.audio_recorder = AudioRecorder(sample_rate, channels)
        self.speech_recognizer = SpeechRecognizer()
        self.tts_data = TTSData()

        # 初始化配置驱动的文本处理器
        self.text_processor = get_text_processor()
        self.config_loader = get_config_loader()
        _logger.info("✅ 文本处理器初始化完成")

        # 音频流相关属性
        self.vad_queue = deque(maxlen=vad_queue_size)  # VAD队列
        self.stream_active = False
        self.stream_thread = None
        self.vad_thread = None
        self.vad_processing = False
        self.conversation_state = ConversationState.WAIT_FOR_WAKEUP  # 对话状态
        self.silence_duration_threshold = 15  # 10s
        self.future_play_end_time = 0
        self.min_speech_energy = 0.025

        # 添加线程锁，保护队列的并发访问
        self.vad_queue_lock = threading.Lock()
        self.other_queue_lock = threading.Lock()

        _logger.info("✅ 音频流读取器初始化完成")
        _logger.info(f"   ASR服务器: {asr_server_url}")
        _logger.info(f"   采样率: {sample_rate}Hz")
        _logger.info(f"   通道数: {channels}")
        _logger.info(f"   音频设备ID: {self.device_id}")
        _logger.info(f"   VAD队列大小: {vad_queue_size}")
        _logger.info(f"   VAD最小chunk数: {vad_min_chunks}")
        _logger.info(f"   最大语音时长: {max_speech_duration}秒")
        _logger.info(f"   最大处理时间: {max_processing_time}秒")

    def start_audio_stream(self):
        """
        启动音频流读取器，持续读取chunk并存入两个队列
        """
        if self.stream_active:
            _logger.warning("⚠️ 音频流已经在运行中")
            return

        _logger.info("🎙️ 启动音频流读取器...")
        self.stream_active = True

        # 启动音频流读取线程
        self.stream_thread = threading.Thread(
            target=self._audio_stream_worker, daemon=True)
        self.stream_thread.start()

        # 启动VAD处理线程
        self.vad_thread = threading.Thread(
            target=self._vad_worker, daemon=True)
        self.vad_thread.start()

        _logger.info("✅ 音频流读取器启动成功")

    def stop_audio_stream(self):
        """
        停止音频流读取器
        """
        if not self.stream_active:
            _logger.warning("⚠️ 音频流未在运行")
            return

        _logger.info("🛑 停止音频流读取器...")
        self.stream_active = False
        self.vad_processing = False

        # 等待线程结束
        if self.stream_thread and self.stream_thread.is_alive():
            self.stream_thread.join(timeout=2)
            # 如果线程仍在运行，强制杀死
            if self.stream_thread.is_alive():
                _logger.warning("⚠️ 音频流线程未正常结束，强制杀死")
                try:
                    import ctypes
                    thread_id = self.stream_thread.ident
                    if thread_id:
                        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                            ctypes.c_long(thread_id),
                            ctypes.py_object(SystemExit)
                        )
                        if res > 1:
                            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                                thread_id, 0)
                            _logger.error("❌ 强制杀死音频流线程失败")
                        else:
                            _logger.info("✅ 强制杀死音频流线程成功")
                except Exception as e:
                    _logger.error(f"❌ 强制杀死音频流线程时出错: {e}")

        if self.vad_thread and self.vad_thread.is_alive():
            self.vad_thread.join(timeout=2)
            # 如果线程仍在运行，强制杀死
            if self.vad_thread.is_alive():
                _logger.warning("⚠️ VAD线程未正常结束，强制杀死")
                try:
                    import ctypes
                    thread_id = self.vad_thread.ident
                    if thread_id:
                        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                            ctypes.c_long(thread_id),
                            ctypes.py_object(SystemExit)
                        )
                        if res > 1:
                            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                                thread_id, 0)
                            _logger.error("❌ 强制杀死VAD线程失败")
                        else:
                            _logger.info("✅ 强制杀死VAD线程成功")
                except Exception as e:
                    _logger.error(f"❌ 强制杀死VAD线程时出错: {e}")

        _logger.info("✅ 音频流读取器已停止")

    def _read_with_timeout(self, stream, chunk_size, timeout=5.0):
        """
        带超时的音频流读取函数
        
        Args:
            stream: 音频输入流
            chunk_size: 读取的chunk大小
            timeout: 超时时间（秒）
            
        Returns:
            tuple: (chunk, overflowed) 或 (None, None) 如果超时
        """
        result = [None, None]
        exception = [None]
        
        def read_worker():
            try:
                result[0], result[1] = stream.read(chunk_size)
            except Exception as e:
                exception[0] = e
        
        read_thread = threading.Thread(target=read_worker, daemon=True)
        read_thread.start()
        read_thread.join(timeout=timeout)
        
        if read_thread.is_alive():
            # 读取超时
            _logger.warning(f"⚠️ 音频流读取超时（{timeout}秒）")
            return None, None
        
        if exception[0]:
            raise exception[0]
        
        return result[0], result[1]

    def _audio_stream_worker(self):
        """
        音频流读取工作线程
        """
        # 重连相关参数
        max_reconnect_attempts = 5  # 最大重连次数
        reconnect_delay = 1.0  # 重连延迟（秒）
        read_timeout = 5.0  # 读取超时时间（秒）
        reconnect_count = 0
        self.stream_active = True
        try:
            chunk_duration_ms = 30  # 30ms chunks
            chunk_size = int(self.sample_rate * chunk_duration_ms / 1000)
            # 如果使用ALSA流读取器
            if hasattr(self, 'use_alsa_stream') and self.use_alsa_stream:
                _logger.info("🎤 使用ALSA直接读取音频流")
                if not self.alsa_stream.start():
                    _logger.error("❌ 启动ALSA音频流失败")
                    return
            else:
                _logger.info("🎤 使用SD读取音频流")

                # 重连循环
                while self.stream_active and reconnect_count < max_reconnect_attempts:
                    try:
                        # 每次重连前，重新获取并校验输入设备，避免使用失效的 device_id
                        try:
                            self.device_id = get_audio_device_id()
                            devices = sd.query_devices()
                            if self.device_id is None or self.device_id >= len(devices):
                                _logger.error(
                                    f"❌ 当前获取到的设备ID无效: {self.device_id}，停止重连")
                                break

                            device = devices[self.device_id]
                            max_input_channels = device.get(
                                "max_input_channels", 0)
                            if max_input_channels == 0:
                                _logger.error(
                                    f"❌ 设备 {self.device_id} ({device.get('name', 'unknown')}) 不支持音频输入，停止重连")
                                break

                            stream_params = {
                                'device': self.device_id,
                                'channels': self.channels,
                                'samplerate': self.sample_rate,
                                'dtype': 'float32',
                                'blocksize': int(self.sample_rate * 0.03)  # 30ms blocks
                            }

                            _logger.info(
                                f"🎤 使用设备 {self.device_id} - {device.get('name', 'unknown')} 重新建立音频流")
                        except Exception as e:
                            _logger.error(
                                f"❌ 重连前重新获取音频设备信息失败: {e}")
                            break

                        stream = None
                        try:
                            stream = sd.InputStream(**stream_params)
                            stream.start()
                            _logger.info("✅ 音频流连接成功")
                            reconnect_count = 0  # 重置重连计数
                            
                            while self.stream_active:
                                try:
                                    # 使用带超时的读取
                                    chunk, overflowed = self._read_with_timeout(
                                        stream, chunk_size, read_timeout)
                                    
                                    # 检查是否超时
                                    if chunk is None:
                                        _logger.warning("⚠️ 音频流读取超时，准备重连")
                                        break
                                    
                                    # vad = VoiceActivityDetector(
                                    #     sample_rate=self.sample_rate,
                                    #     energy_threshold=self.min_speech_energy,  # 降低阈值，更敏感
                                    #     silence_duration_ms=self.silence_duration_ms,  # 降低静音检测阈值
                                    #     min_speech_duration_ms=150,  # 减少最小语音时长
                                    #     speech_detection_threshold_ms=100  # 减少检测阈值，更快检测
                                    # )
                                    # energy = vad.calculate_frame_energy(chunk)
                                    # _logger.info(f"🔊 当前帧能量: {energy}", f"🔊 当前帧能量阈值: {vad.energy_threshold}")

                                    if overflowed:
                                        _logger.warning("⚠️ 音频缓冲区溢出")

                                    # 将chunk存入两个队列
                                    with self.vad_queue_lock:
                                        self.vad_queue.append(chunk.copy())

                                    # 可选：显示队列状态
                                    if len(self.vad_queue) % 100 == 0:  # 每100个chunk显示一次
                                        with self.vad_queue_lock:
                                            vad_size = len(self.vad_queue)

                                except Exception as e:
                                    _logger.error(f"❌ 读取音频chunk时出错: {e}")
                                    break  # 跳出内层循环，准备重连
                        
                        except Exception as e:
                            _logger.error(
                                f"❌ 创建或启动音频流失败: {e!r}（device_id={self.device_id}）")
                            raise
                        finally:
                            # 确保关闭当前流
                            if stream is not None:
                                try:
                                    stream.stop()
                                    stream.close()
                                    _logger.info("🔌 已关闭音频流")
                                except Exception as e:
                                    _logger.warning(f"⚠️ 关闭音频流时出错: {e}")
                        
                        # 如果 stream_active 为 False，退出重连循环
                        if not self.stream_active:
                            break
                        # 准备重连
                        reconnect_count += 1
                        if reconnect_count < max_reconnect_attempts:
                            _logger.info(f"🔄 准备重连音频流（第 {reconnect_count}/{max_reconnect_attempts} 次）...")
                            time.sleep(reconnect_delay)
                        else:
                            _logger.error(f"❌ 达到最大重连次数（{max_reconnect_attempts}），停止重连")
                            break
                    
                    except Exception as e:
                        _logger.error(f"❌ 音频流重连过程中出错: {e}")
                        reconnect_count += 1
                        if reconnect_count < max_reconnect_attempts:
                            time.sleep(reconnect_delay)
                        else:
                            break

        except Exception as e:
            _logger.error(f"❌ 音频流工作线程出错: {e}")
            # 异常后强制停止线程
            self.stream_active = False
        finally:
            _logger.info("🔚 音频流工作线程结束")
            # 确保线程完全停止
            self.stream_active = False

    def is_static_enough(self, audio_data: AudioData):
        """
        判断音频数据是否足够静态（静音）
        
        条件检查：
        1. VAD类型必须是静止（static）
        2. 对话播放状态的is_playing_now为False，并且VAD持续时间大于静音阈值
        """
        # 条件1：VAD类型必须是静止（static）
        condition1 = audio_data.vad_type == "static"
        
        if not condition1:
            return False
        
        # 条件2：对话播放状态检查
        conversation_playing_status = self.get_conversation_playing_status()
        is_not_playing = conversation_playing_status.get('is_playing_now', True) == False
        duration_enough = audio_data.vad_duration > 180
        condition2 = is_not_playing and duration_enough
        
        # 所有条件都必须满足
        return condition1 and condition2

    def _vad_worker(self):
        """
        VAD处理工作线程 - 实时处理模式
        """
        try:
            _logger.info("🎤 VAD实时处理线程启动")

            while self.stream_active:
                try:
                    # 实时处理：只要有少量数据就开始处理
                    with self.vad_queue_lock:
                        vad_queue_size = len(self.vad_queue)

                    # 调整阈值：确保有足够数据检测静音结束
                    # 静音检测时长：800ms，需要 800ms ÷ 30ms = 27个chunk
                    # 使用配置的最小chunk数，确保有足够数据
                    if vad_queue_size < self.vad_min_chunks:
                        time.sleep(0.05)  # 等待50ms，更频繁检查
                        continue

                    # 开始VAD处理
                    self.vad_processing = True
                    processing_start_time = time.time()

                    # 从队列中获取音频数据进行VAD处理
                    audio_data = self.get_full_vad_from_queue()

                    # 检查处理时间是否超时
                    if self.is_in_conversation():
                        if self.is_static_enough(audio_data):
                            _logger.info(f"🔍 检测到静音超时")
                            self.clear_queues(self.vad_queue_lock, self.vad_queue)
                            self.handle_recognized_text(
                                "", audio_data)
                    if audio_data.audio_data is not None:
                        _logger.info(f"🔍 VAD检测结果: audio_data不为None, vad_type={audio_data.vad_type}")
                        if audio_data.vad_type == "speech":
                            _logger.info("🎤 检测到语音，准备创建临时文件...")
                            temp_file = self.save_audio_to_temp_file(
                                audio_data=audio_data.audio_data)
                            _logger.info(f"💾 临时文件已创建: {temp_file}")
                            recognized_text = self.speech_recognizer.recognize(
                                temp_file, self.asr_server_url)
                            self.remove_temp_file(temp_file)
                        else:
                            _logger.info(
                                f"🔇 检测到静音，vad_type={audio_data.vad_type}")
                            recognized_text = ""
                        if recognized_text:
                            self.handle_recognized_text(
                                recognized_text, audio_data)
                        # 这里可以添加语音检测到后的处理逻辑
                        # 例如：唤醒词检测、语音识别等
                    # else:
                    #     _logger.info("🔇 audio_data为None，跳过处理")
                    self.vad_processing = False
                    # 实时处理：减少等待时间
                    time.sleep(0.1)  # 等待100ms，更频繁处理

                except Exception as e:
                    _logger.error(f"❌ 实时VAD处理出错: {e}")
                    # self.vad_processing = False
                    # # 异常后强制停止线程
                    # self.stream_active = False
                    continue

        except Exception as e:
            _logger.error(f"❌ VAD实时工作线程出错: {e}")
            # 异常后强制停止线程
            self.stream_active = False
        finally:
            _logger.info("🔚 VAD实时工作线程结束")
            # 确保线程完全停止
            self.stream_active = False

    def handle_recognized_text(self, recognized_text: str, audio_data: AudioData):
        if self._is_stop_conversation_word(recognized_text):
            # 检测到停止对话词 - 优先级高于唤醒词
            if self.is_in_conversation():
                # 根据对话类型播放不同的提示
                if self.conversation_state == ConversationState.IN_WAKEUP_CONVERSATION:
                    self._synthesize_and_play_text("语音对话结束. 请重新唤醒我")
                    conversation_playing_status = self.get_conversation_playing_status()
                    while conversation_playing_status['is_playing_now'] == True:
                        time.sleep(0.1)
                        conversation_playing_status = self.get_conversation_playing_status()
                    self.end_conversation()
                _logger.info("✅ 已通过停止对话词结束对话")
                return
        elif self.is_static_enough(audio_data):
            _logger.info(f"🔍 检测到静音超时")
            # 静音超时，只对语音唤醒对话结束
            if self.conversation_state == ConversationState.IN_WAKEUP_CONVERSATION:
                self._synthesize_and_play_text("语音对话结束. 请重新唤醒我")
                conversation_playing_status = self.get_conversation_playing_status()
                while conversation_playing_status['is_playing_now'] == True:
                    time.sleep(0.1)
                    conversation_playing_status = self.get_conversation_playing_status()
                self.end_conversation()
                return
            elif self.conversation_state == ConversationState.IN_BUTTON_CONVERSATION:
                # 按钮对话不会因为超时而结束，需要手动结束
                _logger.info("🔘 按钮对话模式：静音超时，但不会自动结束对话")
                return
        # 检测唤醒词并提取问题
        wake_word_info = self._extract_wake_word_and_query(recognized_text)
        
        if wake_word_info['has_wake_word']:
            # 确保进入对话模式
            if self.conversation_state == ConversationState.WAIT_FOR_WAKEUP:
                self.start_wakeup_conversation()
            
            if self.conversation_state == ConversationState.IN_WAKEUP_CONVERSATION:
                if wake_word_info['is_wake_word_only']:
                    # 情况1：只含唤醒词，按现有逻辑处理
                    reply_success = self._synthesize_and_play_text(
                        "music/nihao.wav", use_path=True)
                    if reply_success:
                        duration = self._get_response_duration(reply_success)
                        self.future_play_end_time = time.time() + duration
                        _logger.info("✅ 语音唤醒成功，进入对话模式")
                    else:
                        _logger.error("❌ 语音唤醒回复播放失败")
                        self.end_conversation()
                else:
                    # 情况2：唤醒词+问题，直接处理问题
                    query_text = wake_word_info['query_text']
                    if query_text and query_text.strip():
                        _logger.info(f"🎤 检测到唤醒词+问题: '{query_text}'")
                        
                        # 文本处理（口语数字标准化等）
                        query_text_before = query_text
                        query_text = self.text_processor.normalize_spoken_digits(query_text)
                        if query_text != query_text_before:
                            _logger.info(f"🔄 口语数字标准化: '{query_text_before}' → '{query_text}'")
                        
                        # 添加车辆编号
                        # query_text = self._add_vehile_num(query_text)
                        
                        # 播放"请稍等"提示音
                        reply_qing_nin_shao_deng = self._synthesize_and_play_text(
                            "music/qing_nin_shao_deng.wav", use_path=True)
                        
                        # 发送问题给AI
                        ai_response = self._request_server(self.dialog_ai_url, query_text)
                        response = self._synthesize_and_play_text(ai_response)
                        
                        if response:
                            duration = self._get_response_duration(response=response)
                            self.future_play_end_time = time.time() + duration
                            response_data = self._get_response_data(response)
                            _logger.info(
                                f"🔊 回复播放完成，回复内容: {response_data}, 时长: {duration:.2f}")
                        else:
                            self.future_play_end_time = 0
                            _logger.error("❌ 回复播放失败")
                    else:
                        # 提取的问题为空，降级为只含唤醒词的处理
                        _logger.warning("⚠️ 提取的问题为空，按只含唤醒词处理")
                        reply_success = self._synthesize_and_play_text(
                            "music/nihao.wav", use_path=True)
                        if reply_success:
                            duration = self._get_response_duration(reply_success)
                            self.future_play_end_time = time.time() + duration
                            _logger.info("✅ 语音唤醒成功，进入对话模式")
                        else:
                            _logger.error("❌ 语音唤醒回复播放失败")
                            self.end_conversation()
        elif self.is_in_conversation():
            # 在对话状态中处理用户输入
            _logger.info(
                f"🔊 识别为对话模式，回复内容: {recognized_text}, 开始时间: {audio_data.vad_start_time:.2f}秒, 未来播放结束时间: {self.future_play_end_time:.2f}秒")

            # 检查是否在播放语音期间
            conversation_playing_status = self.get_conversation_playing_status()
            if conversation_playing_status['is_playing_now']:
                _logger.warning(
                    f"❌ 识别为播放语音，不进行播放,{self.future_play_end_time - audio_data.vad_start_time},{audio_data.vad_duration}")
                return
            if conversation_playing_status['is_playing_now'] == False and (conversation_playing_status["last_play_end_time"] - audio_data.vad_start_time) / audio_data.vad_duration > 0.3:
                return           
            # self.future_play_end_time = conversation_playing_status['last_play_end_time']
            # _logger.info(f"🔊 对话播放结束时间: {self.future_play_end_time}")
            # if (self.future_play_end_time - audio_data.vad_start_time) / audio_data.vad_duration > 0.5:
            #     _logger.warning(
            #         f"❌ 识别为播放语音，不进行播放,{self.future_play_end_time - audio_data.vad_start_time},{audio_data.vad_duration}")
            #     return
            
            # ========== 口语数字标准化（新增）==========
            # 将口语数字转换为标准数字，如"幺"→"一"、"洞"→"零"等
            recognized_text_before = recognized_text
            recognized_text = self.text_processor.normalize_spoken_digits(recognized_text)
            if recognized_text != recognized_text_before:
                _logger.info(f"🔄 口语数字标准化: '{recognized_text_before}' → '{recognized_text}'")
            # 发送到AI服务器获取回复
            # recognized_text = self._add_vehile_num(recognized_text)
            reply_qing_nin_shao_deng = self._synthesize_and_play_text("music/qing_nin_shao_deng.wav", use_path=True)
            ai_response = self._request_server(self.dialog_ai_url, recognized_text)
            response = self._synthesize_and_play_text(ai_response)
            
            if response:
                duration = self._get_response_duration(response=response)
                self.future_play_end_time = time.time() + duration
                response_data = self._get_response_data(response)
                _logger.info(
                    f"🔊 ！！！！！！！！！！回复播放完成，回复内容: {response_data}, 时长: {duration:.2f}")
            else:
                self.future_play_end_time = 0
                _logger.error("❌ 回复播放失败")
        else:
            _logger.error(f"❌ 未在对话模式中，当前状态: {self.conversation_state}")

    # ========== 以下方法已被配置驱动的文本处理器替换 ==========
    # 新的处理逻辑在 config/text_processor.py 中
    # 配置文件在 config/rules/ 目录下
    # 保留这些方法以防需要回退

    def _remove_chars(self, text, chars_to_remove=['*', '_', '#','/']):
        """【已废弃】使用 config/rules/special_chars_removal.csv 替代"""
        for char in chars_to_remove:
            text = text.replace(char, '')
        return text

    def _process_abbreviations(self, text: str) -> str:
        """【已废弃】使用 config/rules/abbreviations.csv 替代"""
        import re
        
        abbreviations = ['AGV', 'AMR', 'PLC', 'RFID', 'HMI', 'API', 'GPS', 'USB']
        pattern = r'(?<![a-zA-Z])(' + '|'.join(abbreviations) + r')(?![a-zA-Z])'
        
        def replace_func(match):
            return ' ' + ' '.join(match.group(1).upper()) + ' '
        
        result = re.sub(pattern, replace_func, text, flags=re.IGNORECASE)
        return re.sub(r'\s+', ' ', result).strip()

    def _process_percentages(self, text: str) -> str:
        """【已废弃】使用 config/text_processor.py 中的统一处理逻辑替代"""
        """
        处理百分比格式，确保TTS正确朗读
        
        规则：
        1. 将 "数字%" 转换为 "百分之 X X 点 X X"
        2. 小数保留两位小数
        3. 数字之间插入空格
        4. 整数不带小数点
        
        示例：
        - "0.52152%" -> " 百分之 0 点 5 2 "
        - "95.678%" -> " 百分之 9 5 点 6 8 "
        - "100%" -> " 百分之 1 0 0 "
        """
        import re
        
        def format_percentage(match):
            number_str = match.group(1)
            try:
                # 转换为浮点数
                number = float(number_str)
                # 保留两位小数
                formatted_number = round(number, 2)
                
                # 判断是否为整数（去除.0的情况）
                if formatted_number == int(formatted_number):
                    # 纯整数：每个数字之间加空格
                    result = ' '.join(str(int(formatted_number)))
                else:
                    # 有小数部分
                    number_text = str(formatted_number)
                    integer_part, decimal_part = number_text.split('.')
                    # 整数部分：每个数字之间加空格
                    integer_spaced = ' '.join(integer_part)
                    # 小数部分：每个数字之间加空格
                    decimal_spaced = ' '.join(decimal_part)
                    # 组合：整数 点 小数
                    result = f"{integer_spaced} 点 {decimal_spaced}"
                
                # 返回格式化后的字符串，前后加空格
                return f" 百分之 {result} "
            except ValueError:
                # 如果转换失败，保持原样
                return match.group(0)
        
        # 匹配数字（包括小数）+ %
        pattern = r'(\d+\.?\d*)%'
        result = re.sub(pattern, format_percentage, text)
        
        return result
    
    def _process_decimals(self, text: str) -> str:
        """【已废弃】使用 config/text_processor.py 中的统一处理逻辑替代"""
        """
        处理所有数字和小数，确保TTS正确朗读
        
        规则：
        1. 小数超过2位 -> 保留2位
        2. 小数≤2位 -> 保持不变
        3. 智能识别小数点 vs 句号（前后都是数字才是小数点）
        4. 数字之间插入空格
        5. 小数点转换为"点"
        6. 数字前后添加空格（与文字分隔）
        
        示例：
        - "3.14159" -> " 3 点 1 4 "
        - "3.5" -> " 3 点 5 "
        - "任务完成. 继续" -> "任务完成. 继续"（句号保持）
        """
        import re
        
        def is_decimal_point(text, pos):
            """判断指定位置的'.'是小数点还是句号"""
            # 向前查找第一个非空格字符
            before_char = None
            for i in range(pos - 1, -1, -1):
                if text[i] != ' ':
                    before_char = text[i]
                    break
            
            # 向后查找第一个非空格字符
            after_char = None
            for i in range(pos + 1, len(text)):
                if text[i] != ' ':
                    after_char = text[i]
                    break
            
            # 判断：前后都是数字才是小数点
            return (before_char is not None and before_char.isdigit() and
                    after_char is not None and after_char.isdigit())
        
        def format_number(match):
            """格式化单个数字（包括小数）"""
            number_str = match.group(0)
            
            try:
                # 检查是否包含小数点
                if '.' in number_str:
                    # 验证是否真的是小数点
                    dot_pos = text.find(number_str, match.start())
                    if dot_pos >= 0:
                        actual_dot_pos = dot_pos + number_str.index('.')
                        if not is_decimal_point(text, actual_dot_pos):
                            # 不是小数点，是句号，保持原样
                            return number_str
                    
                    # 是小数点，处理小数
                    number = float(number_str)
                    
                    # 分离整数和小数部分
                    integer_part = int(number)
                    decimal_part = number - integer_part
                    
                    # 计算小数位数
                    decimal_str = str(number).split('.')[1] if '.' in str(number) else ""
                    
                    # 如果小数超过2位，保留2位
                    if len(decimal_str) > 2:
                        number = round(number, 2)
                        number_str = str(number)
                    
                    # 重新分离
                    if '.' in number_str:
                        int_part, dec_part = number_str.split('.')
                        # 整数部分：数字间加空格
                        int_spaced = ' '.join(int_part)
                        # 小数部分：数字间加空格
                        dec_spaced = ' '.join(dec_part)
                        # 前后添加空格
                        return f" {int_spaced} 点 {dec_spaced} "
                    else:
                        # 四舍五入后变成整数，前后添加空格
                        return f" {' '.join(number_str)} "
                else:
                    # 纯整数：数字间加空格，前后也加空格
                    return f" {' '.join(number_str)} "
                    
            except ValueError:
                # 转换失败，保持原样
                return number_str
        
        # 匹配所有数字（包括小数）
        # 注意：这里匹配数字，但不包括已经处理过的百分比
        pattern = r'\d+\.?\d*'
        result = re.sub(pattern, format_number, text)
        
        return result

    def _get_reply_text(self,text:str):
        text = text.strip('{}')
        for pair in text.split(',',1):
            key, value = pair.split('=', 1)
            if key.strip() == 'resultMsg':
                # 去掉值的引号并返回
                return value
        return None  # 如果未找到 resultMsg，返回 None

    def _add_vehile_num(self,text:str) -> str:
        text = '车辆' + self.nick_name + ',' + text
        return text

    def _request_server(self, url: str, text: str):
        """
        向服务器发送查询请求并返回响应

        Args:
            url: 服务器URL。如果为 None，则视为推送语音注入，直接返回处理后的原文本。
            text: 查询文本或推送文本

        Returns:
            str: 服务器响应的resultMsg内容，或处理后的推送文本
        """
        try:
            if url is None:
                # 融入逻辑：如果是推送消息，模拟服务器响应数据结构
                ai_reply = text
                response_data = {"resultCode": 0, "resultMsg": text, "source": "external_push"}
                _logger.info(f"📥 接收到推送语音内容，正在融入主流程处理...")
            else:
                # 构造请求数据
                request_data = {
                    "query": text
                }
                # 发送POST请求
                response = requests.post(
                    url,
                    json=request_data,
                    timeout=30  # 30秒超时
                )
                # 检查响应状态
                response.raise_for_status()  # 检查 HTTP 状态码
                
                # 解析 JSON 响应（标准 JSON 格式）
                response_data = response.json()
                
            # 验证响应格式并直接返回resultMsg
            if "resultCode" in response_data and "resultMsg" in response_data:
                ai_reply = response_data["resultMsg"]  # 直接取字段
            else:
                _logger.warning(f"⚠️ 服务器响应格式异常（缺少 resultCode/resultMsg）: {response_data}")
                return "服务器响应格式异常"
                
            # --- 统一的后续处理逻辑（清洗、记录、保存） ---
            # 使用配置驱动的文本处理器
            ai_reply = self.text_processor.process_text(ai_reply)
            
            # 清理多余的空格
            import re
            ai_reply = re.sub(r'\s+', ' ', ai_reply).strip()
            
            # 记录成功日志
            if url:
                _logger.info(f"✅ 服务器响应成功: resultCode={response_data.get('resultCode')}, resultMsg={ai_reply[:100]}...")
            
            # 保存用户请求和AI回复作为证据（推送消息也会被记录）
            # self._save_ai_response_text(text, ai_reply, str(response_data))
                
            return ai_reply
        except requests.exceptions.ConnectionError as e:
            _logger.error(f"❌ 服务器连接失败: 无法连接到 {url} - {e}")
            return "服务器连接失败"
        except requests.exceptions.Timeout as e:
            _logger.error(f"❌ 服务器连接超时: {url} 响应超时 - {e}")
            return "服务器连接超时"
        except requests.exceptions.HTTPError as e:
            _logger.error(f"❌ HTTP错误: {e}")
            return f"服务器错误: {e}"
        except ValueError as e:
            _logger.error(f"❌ JSON解析失败: {e}, 响应内容: {response.text[:200]}")
            return "服务器响应格式错误"
        except requests.exceptions.RequestException as e:
            _logger.error(f"❌ 请求异常: {e}")
            return "请求异常"
        except Exception as e:
            _logger.error(f"❌ 未知错误: {e}")
            return "未知错误"

    def _save_ai_response_text(self, user_request: str, ai_response: str, response_data: str):
        """
        保存用户请求和AI回复文本到CSV文件作为证据
        
        功能：
        1. 将所有对话按顺序保存到单个CSV文件中
        2. CSV文件最大支持900MB，超过后删除重新开始
        3. CSV包含：时间、用户请求、AI回复、response_data
        
        参数：
            user_request: 用户的请求文本
            ai_response: AI的回复文本（经过文本处理后的最终结果）
            response_data: 原始服务器响应数据
        """
        try:
            # 创建保存目录
            save_dir = Path(__file__).parent.parent / "ai_responses"
            save_dir.mkdir(exist_ok=True)
            
            # CSV文件名
            csv_filename = "ai_response.csv"
            csv_filepath = save_dir / csv_filename
            
            # 检查文件大小，超过900MB则删除重新开始
            max_size_bytes = 900 * 1024 * 1024  # 900MB
            file_exists = csv_filepath.exists()
            if file_exists:
                file_size = os.path.getsize(csv_filepath)
                if file_size >= max_size_bytes:
                    csv_filepath.unlink()
                    file_exists = False  # 文件已被删除
                    _logger.info(f"🗑️ CSV文件超过900MB，已删除并重新开始: {csv_filepath.name}")
            
            # 准备数据
            timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # 写入CSV文件（追加模式）
            with open(csv_filepath, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                
                # 如果文件不存在，写入表头
                if not file_exists:
                    writer.writerow(['时间', '用户请求', 'AI回复', 'response_data'])
                
                # 写入数据行
                writer.writerow([timestamp, user_request, ai_response, response_data])
            
            _logger.info(f"💾 对话记录已保存到CSV: {csv_filename}")
            
        except Exception as e:
            _logger.error(f"❌ 保存对话记录失败: {e}")

    def save_audio_to_temp_file(self, audio_data: np.ndarray):
        import numpy as np
        timestamp = int(time.time())
        temp_file = f"temp_vad_recording_{timestamp}.wav"
        _logger.info(f"🔧 开始创建临时文件: {temp_file}")
        _logger.info(f"   音频数据形状: {audio_data.shape}")
        _logger.info(f"   音频数据类型: {audio_data.dtype}")

        # 1. 变慢（拉长1.2倍）
        slow_factor = 1.1  # 变慢20%
        if len(audio_data.shape) == 1:
            # 单通道
            new_length = int(len(audio_data) * slow_factor)
            audio_data = np.interp(np.linspace(
                0, len(audio_data) - 1, new_length), np.arange(len(audio_data)), audio_data)
        elif len(audio_data.shape) == 2:
            # 多通道，对每个通道分别处理
            new_length = int(audio_data.shape[0] * slow_factor)
            audio_data = np.stack([
                np.interp(np.linspace(0, len(audio_data[:, ch]) - 1, new_length), np.arange(len(audio_data[:, ch])), audio_data[:, ch]) for ch in range(audio_data.shape[1])
            ], axis=1)
        # 2. 放大音量（2倍）
        volume_factor = 1.5
        audio_data = np.clip(audio_data * volume_factor, -1.0, 1.0)
        # 3. 转为int16保存
        audio_int16 = (audio_data * 32767).astype(np.int16)
        _logger.info(f"   处理后的音频数据形状: {audio_int16.shape}")
        _logger.info(f"   处理后的音频数据类型: {audio_int16.dtype}")

        try:
            wavfile.write(temp_file, self.sample_rate, audio_int16)
            _logger.info(f"✅ 临时文件保存成功: {temp_file}")
            _logger.info(f"   文件大小: {os.path.getsize(temp_file)} 字节")
            return temp_file
        except Exception as e:
            _logger.error(f"❌ 保存临时文件失败: {e}")
            return None

    def remove_temp_file(self, file_path: str):
        if os.path.exists(file_path):
            os.remove(file_path)
            _logger.info(f"🗑️ 已清理临时文件: {file_path}")

    def get_full_vad_from_queue(self) -> Optional[AudioData]:
        """
        从VAD队列中实时提取语音片段 - 边采样边处理

        Returns:
            Optional[np.ndarray]: 提取的语音数据，如果没有检测到语音则返回None
        """
        try:
            # 第一次获取锁：检查队列状态和获取音频数据
            with self.vad_queue_lock:
                # 调整阈值：确保有足够数据检测静音结束
                # 静音检测时长：800ms，需要 800ms ÷ 30ms = 27个chunk
                # 使用配置的最小chunk数，确保有足够数据

                if len(self.vad_queue) < self.vad_min_chunks:
                    return None
                # 实时处理：使用队列中的所有可用数据
                audio_chunks = list(self.vad_queue)
                total_duration = len(audio_chunks) * 30 / 1000  # 30ms per chunk

            # 释放锁后进行音频处理（不需要锁保护）
            # 合并音频数据
            audio_data = np.concatenate(audio_chunks, axis=0)

            # 使用单声道进行VAD检测
            if self.channels > 1:
                mono_audio = audio_data[:, 0]
            else:
                mono_audio = audio_data.flatten()

            min_speech_energy = self.min_speech_energy
            if self.conversation_state == ConversationState.WAIT_FOR_WAKEUP:
                min_speech_energy = self.min_speech_energy*2.5
            if self.conversation_state == ConversationState.IN_WAKEUP_CONVERSATION:
                min_speech_energy = self.min_speech_energy*4.0
            if self.conversation_state == ConversationState.WAIT_FOR_WAKEUP:
                self.silence_duration_ms = 655
            # 初始化VAD检测器 - 使用更敏感的参数
            vad = VoiceActivityDetector(
                sample_rate=self.sample_rate,  # 使用48000Hz
                energy_threshold=min_speech_energy,  # 降低阈值，更敏感
                silence_duration_ms=self.silence_duration_ms,  # 降低静音检测阈值
                min_speech_duration_ms=150,  # 减少最小语音时长
                speech_detection_threshold_ms=100  # 减少检测阈值，更快检测
            )

            # 分帧处理
            frame_duration_ms = 30
            # 计算每个音频帧包含多少个采样点（samples）
            frame_size = int(self.sample_rate * frame_duration_ms / 1000)

            speech_start_frame = -1
            speech_end_frame = -1
            speech_detected = False
            beyond_max_speech_duration = False

            # 实时检测：从音频开始位置向前扫描
            # 这样可以按时间顺序检测语音活动
            speech_start_time = None  # 记录语音开始的时间
            total_audio_duration = len(mono_audio) / self.sample_rate  # 音频总时长
            for i in range(0, len(mono_audio) - frame_size, frame_size):
                frame = mono_audio[i:i + frame_size]
                if len(frame) < frame_size:
                    continue

                # 使用detect_speech_start检测语音开始
                speech_started = vad.detect_speech_start(frame)

                # 检测到语音开始
                if speech_started and speech_start_frame == -1:
                    # 往前推speech_detection_threshold_ms毫秒，找到真正的语音开始位置
                    offset_ms = 200
                    samples_to_go_back = int(
                        self.sample_rate * (vad.speech_detection_threshold_ms+offset_ms) / 1000)
                    speech_start_frame = max(
                        0, i - samples_to_go_back)  # 确保不会小于0
                    speech_detected = True
                    # 计算语音的实际录制开始时间（基于音频数据的时间）
                    # 当前时刻对应音频数据的末尾，需要往前推算

                    speech_start_time_offset = speech_start_frame / self.sample_rate  # 语音开始位置的时间偏移
                    speech_start_time = time.time() - (total_audio_duration -
                                                       speech_start_time_offset)  # 从当前时刻往前推算
                    _logger.info(
                        f"🎤 实时检测到语音开始，检测帧位置: {i}, 实际开始位置: {speech_start_frame}, 总长度: {len(mono_audio)}")
                    _logger.info(
                        f"   往前推了 {vad.speech_detection_threshold_ms}ms ({samples_to_go_back} 个样本)")
                    _logger.info(
                        f"   音频总时长: {total_audio_duration:.2f}秒, 语音开始偏移: {speech_start_time_offset:.2f}秒")
                    _logger.info(
                        f"   语音录制开始时间: {total_audio_duration - speech_start_time_offset:.2f}秒前")
                    break
            zero_frame_time = time.time() - total_audio_duration  # 0时刻
            end_frame_time = time.time()
            if not speech_detected:
                return AudioStreamReader.AudioData(None, "static", len(mono_audio) / self.sample_rate, zero_frame_time, end_frame_time)

            # 重置VAD检测器，用于检测语音结束
            vad.reset()
            speech_ended = False
            # 从语音开始位置向前检测语音结束
            for i in range(speech_start_frame, len(mono_audio), frame_size):
                frame = mono_audio[i:i + frame_size]
                if len(frame) < frame_size:
                    break

                # 检查是否超时（从语音开始算起）
                if speech_start_time:
                    elapsed_time = time.time() - speech_start_time
                    if elapsed_time > self.max_speech_duration:
                        speech_end_frame = i + frame_size
                        _logger.warning(
                            f"⏰ 从语音开始算起{self.max_speech_duration}秒超时，强制结束语音检测，帧位置: {speech_end_frame}")
                        _logger.info(f"   实际语音时长: {elapsed_time:.2f}秒")
                        beyond_max_speech_duration = True
                        break

                # 使用detect_speech_end检测语音结束
                speech_ended = vad.detect_speech_end(frame)

                if speech_ended:
                    speech_end_frame = i + frame_size
                    actual_duration = time.time() - speech_start_time if speech_start_time else 0
                    _logger.info(f"🔇 实时检测到语音结束，帧位置: {speech_end_frame}")
                    _logger.info(f"   实际语音时长: {actual_duration:.2f}秒")
                    break
            
            # 如果循环结束但未检测到明确的语音结束，使用音频末尾作为结束点
            _logger.info(f"###############################################################################speech_end_frame: {speech_end_frame}")
            # if speech_end_frame == -1:
            #     speech_end_frame = len(mono_audio)
            #     actual_duration = time.time() - speech_start_time if speech_start_time else 0
            #     _logger.info(f"⚠️ 未检测到明确的语音结束，使用音频末尾作为结束点")
            #     _logger.info(f"   音频末尾帧位置: {speech_end_frame}")
            #     _logger.info(f"   实际语音时长: {actual_duration:.2f}秒")
            if beyond_max_speech_duration:
                speech_end_frame = len(mono_audio)
                actual_duration = time.time() - speech_start_time if speech_start_time else 0
                _logger.info(f"⚠️ 未检测到明确的语音结束，使用音频末尾作为结束点")
                _logger.info(f"   音频末尾帧位置: {speech_end_frame}")
                _logger.info(f"   实际语音时长: {actual_duration:.2f}秒")
                self._cleanup_processed_chunks(speech_end_frame)
                return AudioStreamReader.AudioData(mono_audio, "speech", len(mono_audio) / self.sample_rate, speech_start_time, time.time())
            # 提取语音片段
            if speech_start_frame >= 0 and speech_end_frame > speech_start_frame:
                speech_audio = mono_audio[speech_start_frame:speech_end_frame]
                speech_duration = len(speech_audio) / self.sample_rate
                actual_time = time.time() - speech_start_time if speech_start_time else 0

                _logger.info(f"✅ 实时提取语音片段，时长: {speech_duration:.2f}秒")
                _logger.info(f"   从语音开始算起: {actual_time:.2f}秒")

                # 第二次获取锁：清理已处理的音频数据
                self._cleanup_processed_chunks(speech_end_frame)

                return AudioStreamReader.AudioData(speech_audio, "speech", speech_duration, speech_start_time, end_frame_time)
            else:
                _logger.error("❌ 无法确定语音边界")
                return AudioStreamReader.AudioData(None, "static", (len(mono_audio) - speech_start_frame) / self.sample_rate, speech_start_time, end_frame_time)

        except Exception as e:
            _logger.error(f"❌ 实时VAD处理出错: {e}")
            return AudioStreamReader.AudioData(None, "static", len(mono_audio) / self.sample_rate, zero_frame_time, end_frame_time)

    def _cleanup_processed_chunks(self, end_frame: int):
        """
        从VAD队列中移除已处理的音频数据

        Args:
            end_frame: 语音结束的样本位置
        """
        try:
            # 计算需要移除的chunk数量
            chunk_duration_ms = 30
            frame_size = int(self.sample_rate * chunk_duration_ms / 1000)

            # 计算从语音开始到结束位置对应的chunk数量
            # 注意：end_frame是样本数，需要转换为chunk数
            total_samples_processed = end_frame
            chunks_to_remove = total_samples_processed // frame_size

            # 确保不超过队列中实际存在的chunk数量
            with self.vad_queue_lock:
                actual_chunks_in_queue = len(self.vad_queue)
                chunks_to_remove = min(
                    chunks_to_remove, actual_chunks_in_queue)

                if chunks_to_remove > 0:
                    # 移除已处理的chunk
                    for _ in range(chunks_to_remove):
                        if self.vad_queue:
                            self.vad_queue.popleft()

                    remaining_chunks = len(self.vad_queue)
                    _logger.info(
                        f"🗑️ 已从队列中移除 {chunks_to_remove} 个chunk，剩余 {remaining_chunks} 个chunk")
                    _logger.info(
                        f"   处理了 {total_samples_processed} 个样本，对应 {chunks_to_remove} 个chunk")
                else:
                    _logger.warning("⚠️ 无需移除chunk，队列可能为空或处理数据量很小")

        except Exception as e:
            _logger.error(f"❌ 清理已处理音频数据出错: {e}")

    def get_queue_status(self) -> Dict[str, int]:
        """
        获取队列状态

        Returns:
            Dict[str, int]: 包含两个队列长度的字典
        """
        with self.vad_queue_lock:
            vad_queue_size = len(self.vad_queue)

        return {
            'vad_queue_size': vad_queue_size,
            'vad_processing': self.vad_processing
        }

    def clear_queues(self, lock, vad_queue: deque):
        """
        清空两个队列
        """
        with lock:
            vad_queue.clear()

    def _start_conversation(self):
        return self.tts_data.start_conversation()

    def _stop_conversation(self):
        return self.tts_data.stop_conversation()

    def get_conversation_playing_status(self):
        return self.tts_data.get_conversation_playing_status()

    def _synthesize_and_play_text(self, text: str, use_path: bool = False) -> bool:
        """
        使用TTS合成文本并播放音频

        Args:
            text: 要合成的文本

        Returns:
            bool: 是否成功
        """
        if not text:
            _logger.error("❌ 文本为空，无法合成")
            return False
        _logger.info(f"🔊 正在播放对话内容: '{text}'")
        return self.tts_data.play_conversation(text, use_path)

    @staticmethod
    def cleanup_temp_file(file_path: str) -> None:
        """清理临时文件"""            # if speech_end_frame == -1:
            #     speech_end_frame = len(mono_audio)
            #     actual_duration = time.time() - speech_start_time if speech_start_time else 0
            #     _logger.info(f"⚠️ 未检测到明确的语音结束，使用音频末尾作为结束点")
            #     _logger.info(f"   音频末尾帧位置: {speech_end_frame}")
            #     _logger.info(f"   实际语音时长: {actual_duration:.2f}秒")
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
                _logger.info(f"🗑️ 已清理临时文件: {file_path}")
        except Exception as e:
            _logger.warning(f"⚠️ 清理临时文件失败: {e}")

    def _is_wake_word(self, recognized_text: str) -> bool:
        """
        检查识别文本是否包含唤醒词（配置驱动）
        
        从 config/rules/wake_words.csv 读取唤醒词配置
        支持精确匹配和模糊匹配
        """
        # 从配置加载器获取唤醒词
        wake_words_data = self.config_loader.get_enabled_items('wake_words')
        
        # 分类唤醒词
        exact_words = [w['wake_word'] for w in wake_words_data if w['word_type'] == 'exact']
        fuzzy_words = [w['wake_word'] for w in wake_words_data if w['word_type'] == 'fuzzy']
        
        # 检查精确匹配
        is_wake_word = any(word in recognized_text for word in exact_words)
        if is_wake_word:
            matched_word = next(word for word in exact_words if word in recognized_text)
            _logger.info(f"🔍 精确匹配检测到唤醒词: '{matched_word}'")
            return True
        
        # 检查模糊匹配
        for word in fuzzy_words:
            if word in recognized_text:
                _logger.info(f"🔍 模糊匹配检测到唤醒词: '{word}'")
                return True
        
        _logger.info(f"🎯 未检测到唤醒词")
        return False
        
    def _extract_wake_word_and_query(self, recognized_text: str) -> dict:
        """
        检测唤醒词并提取问题文本
        
        参数:
            recognized_text: ASR识别的文本
        
        返回:
            dict: {
                'has_wake_word': bool,      # 是否包含唤醒词
                'is_wake_word_only': bool,   # 是否只含唤醒词（无其他有意义内容）
                'query_text': str,           # 提取的问题文本（去除唤醒词后）
                'matched_wake_word': str     # 匹配到的唤醒词
            }
        """
        result = {
            'has_wake_word': False,
            'is_wake_word_only': True,
            'query_text': '',
            'matched_wake_word': ''
        }
        
        if not recognized_text or not recognized_text.strip():
            return result
        
        # 从配置加载器获取唤醒词
        wake_words_data = self.config_loader.get_enabled_items('wake_words')
        
        # 分类唤醒词
        exact_words = [w['wake_word'] for w in wake_words_data if w['word_type'] == 'exact']
        fuzzy_words = [w['wake_word'] for w in wake_words_data if w['word_type'] == 'fuzzy']
        
        matched_wake_word = None
        wake_word_start = -1
        wake_word_end = -1
        
        # 1. 先检查精确匹配
        for word in exact_words:
            if word in recognized_text:
                matched_wake_word = word
                wake_word_start = recognized_text.find(word)
                wake_word_end = wake_word_start + len(word)
                _logger.info(f"🔍 精确匹配检测到唤醒词: '{matched_wake_word}' (位置: {wake_word_start}-{wake_word_end})")
                break
        
        # 2. 如果精确匹配失败，检查模糊匹配
        if not matched_wake_word:
            for word in fuzzy_words:
                if word in recognized_text:
                    matched_wake_word = word
                    wake_word_start = recognized_text.find(word)
                    wake_word_end = wake_word_start + len(word)
                    _logger.info(f"🔍 模糊匹配检测到唤醒词: '{matched_wake_word}' (位置: {wake_word_start}-{wake_word_end})")
                    break
        
        # 3. 如果未找到唤醒词，返回结果
        if not matched_wake_word:
            _logger.debug(f"🎯 未检测到唤醒词")
            return result
        
        # 4. 提取问题文本（移除唤醒词）
        result['has_wake_word'] = True
        result['matched_wake_word'] = matched_wake_word
        
        # 只提取唤醒词后的文本
        query_text = recognized_text[wake_word_end:].strip()
        
        # 5. 清理文本：去除常见标点和空格
        import re
        # 去除前后标点（如：，。！？、等）
        query_text = re.sub(r'^[，。！？、,\.!?\s]+', '', query_text)
        query_text = re.sub(r'[，。！？、,\.!?\s]+$', '', query_text)
        # 去除多余空格
        query_text = re.sub(r'\s+', ' ', query_text).strip()
        
        _logger.debug(f"📝 提取的问题文本: '{query_text}' (原始: '{recognized_text}')")
        
        # 6. 判断文本是否为空（使用新的判断方法）
        if not query_text:
            # 文本为空
            result['is_wake_word_only'] = True
            result['query_text'] = ''
            _logger.info(f"✅ 判断结果: 只含唤醒词（提取后文本为空）")
        else:
            # 使用新的判断方法判断文本是否有意义
            is_meaningful = self._is_text_meaningful(query_text)
            
            if is_meaningful:
                # 文本有意义，包含问题
                result['is_wake_word_only'] = False
                result['query_text'] = query_text
                _logger.info(f"✅ 判断结果: 唤醒词+问题（问题: '{query_text}'）")
            else:
                # 文本无意义，视为只含唤醒词
                result['is_wake_word_only'] = True
                result['query_text'] = ''
                _logger.info(f"✅ 判断结果: 只含唤醒词（提取后文本无意义: '{query_text}'）")
        
        return result
        
    def _is_stop_conversation_word(self, recognized_text: str) -> bool:
        """
        检查识别文本是否包含停止对话词（配置驱动）
        
        从 config/rules/stop_conversation_words.csv 读取停止对话词配置
        支持精确匹配和模糊匹配
        
        参数:
            recognized_text: 识别的文本
        
        返回:
            bool: 是否检测到停止对话词
        """
        # 边界检查：空文本直接返回False
        if not recognized_text or not recognized_text.strip():
            return False
        
        try:
            # 从配置加载器获取停止对话词
            stop_conversation_words_data = self.config_loader.get_enabled_items('stop_conversation_words')
            
            if not stop_conversation_words_data:
                _logger.debug("⚠️ 停止对话词配置为空")
                return False
            
            # 分类停止对话词
            exact_words = [w['stop_conversation_word'] for w in stop_conversation_words_data 
                          if w.get('word_type') == 'exact' and w.get('stop_conversation_word')]
            fuzzy_words = [w['stop_conversation_word'] for w in stop_conversation_words_data 
                          if w.get('word_type') == 'fuzzy' and w.get('stop_conversation_word')]
            
            # 检查精确匹配
            if exact_words:
                matched_exact = [word for word in exact_words if word in recognized_text]
                if matched_exact:
                    matched_word = matched_exact[0]  # 取第一个匹配的词
                    _logger.info(f"🔍 精确匹配检测到停止对话词: '{matched_word}'")
                    return True
            
            # 检查模糊匹配
            if fuzzy_words:
                for word in fuzzy_words:
                    if word in recognized_text:
                        _logger.info(f"🔍 模糊匹配检测到停止对话词: '{word}'")
                        return True
            
            return False
        
        except Exception as e:
            _logger.error(f"❌ 检测停止对话词时出错: {e}")
            return False
    
    def _is_text_meaningful(self, text: str) -> bool:
        """
        判断文本是否包含有意义内容
        
        判断规则：
        1. 纯中文：中文字符数 >= 4 才算有意义
        2. 纯英文：英文字符数 >= 10 才算有意义
        3. 中英文混合：只要满足中文字符 >= 4 或 英文字符 >= 10 其中一个条件，就算有意义
        4. 如果两者都不满足，则认为文本为空（无意义）
        
        参数:
            text: 待判断的文本
        
        返回:
            bool: True表示文本有意义，False表示文本为空或无意义
        """
        if not text or not text.strip():
            return False
        
        # 统计中英文字符数
        chinese_chars = sum(1 for char in text if '\u4e00' <= char <= '\u9fff')
        english_chars = sum(1 for char in text if char.isascii() and char.isalpha())
        
        # 判断文本类型
        has_chinese = chinese_chars > 0
        has_english = english_chars > 0
        
        if has_chinese and has_english:
            # 情况3：中英文混合
            # 只要满足其中一个条件就算有意义：中文 >= 4 或 英文 >= 10
            is_meaningful = (chinese_chars >= 4) or (english_chars >= 10)
            _logger.debug(
                f"📊 混合文本判断: 中文{chinese_chars}个, 英文{english_chars}个, "
                f"结果: {'有意义' if is_meaningful else '无意义'} "
                f"(条件: 中文>={4} 或 英文>={10})"
            )
            return is_meaningful
        elif has_chinese:
            # 情况1：纯中文
            is_meaningful = chinese_chars >= 4
            _logger.debug(
                f"📊 纯中文文本判断: {chinese_chars}个字符, "
                f"结果: {'有意义' if is_meaningful else '无意义'} (需要>={4})"
            )
            return is_meaningful
        elif has_english:
            # 情况2：纯英文
            is_meaningful = english_chars >= 10
            _logger.debug(
                f"📊 纯英文文本判断: {english_chars}个字符, "
                f"结果: {'有意义' if is_meaningful else '无意义'} (需要>={10})"
            )
            return is_meaningful
        else:
            # 既没有中文也没有英文（可能是数字、标点等）
            return False
        
    def _get_response_duration(self, response) -> float:
        """
        安全地从响应中获取duration值

        Args:
            response: HTTP响应对象

        Returns:
            float: duration值，失败时返回0.0
        """
        try:
            if not response:
                return 0.0

            response_data = response.json()
            if not response_data:
                return 0.0

            task = response_data.get('task', {})
            if not task:
                return 0.0

            duration = task.get('duration', 0.0)
            return float(duration) if duration is not None else 0.0

        except (ValueError, TypeError, AttributeError) as e:
            _logger.warning(f"⚠️ 解析响应duration失败: {e}")
            return 0.0
        except Exception as e:
            _logger.error(f"❌ 获取响应duration时出错: {e}")
            return 0.0

    def _get_response_data(self, response) -> dict:
        """
        安全地从响应中获取JSON数据

        Args:
            response: HTTP响应对象

        Returns:
            dict: 响应数据，失败时返回空字典
        """
        try:
            if not response:
                return {}

            response_data = response.json()
            return response_data if response_data else {}

        except (ValueError, TypeError, AttributeError) as e:
            _logger.warning(f"⚠️ 解析响应JSON失败: {e}")
            return {}
        except Exception as e:
            _logger.error(f"❌ 获取响应数据时出错: {e}")
            return {}

    def start_button_conversation(self):
        """
        开始按钮触发的对话（强制切换，无论当前状态）

        Returns:
            bool: 是否成功开始对话
        """
        try:
            _logger.info("🔘 开始按钮触发的对话...")

            # 如果当前已经在按钮对话中，直接返回成功
            if self.conversation_state == ConversationState.IN_BUTTON_CONVERSATION:
                _logger.info("✅ 当前已在按钮对话模式中")
                return True
            # 如果当前在其他对话状态，先结束当前对话
            if self.conversation_state == ConversationState.IN_WAKEUP_CONVERSATION:
                _logger.info("🔄 当前在语音唤醒对话中，强制切换到按钮对话模式")
                self._stop_conversation()
                self.conversation_state = ConversationState.WAIT_FOR_WAKEUP

            # 启动TTS对话模式
            if self._start_conversation():
                self.conversation_state = ConversationState.IN_BUTTON_CONVERSATION
                self.future_play_end_time = 0  # 重置播放结束时间
                _logger.info("✅ 按钮对话模式已启动")
                return True
            else:
                _logger.error("❌ 启动按钮对话模式失败")
                return False

        except Exception as e:
            _logger.error(f"❌ 开始按钮对话时出错: {e}")
            return False

    def start_wakeup_conversation(self):
        """
        开始语音唤醒的对话

        Returns:
            bool: 是否成功开始对话
        """
        try:
            _logger.info("🎤 开始语音唤醒的对话...")

            # 检查当前状态
            if self.conversation_state != ConversationState.WAIT_FOR_WAKEUP:
                _logger.warning(
                    f"⚠️ 当前状态不允许开始语音唤醒对话: {self.conversation_state}")
                return False

            # 启动TTS对话模式
            if self._start_conversation():
                self.conversation_state = ConversationState.IN_WAKEUP_CONVERSATION
                _logger.info("✅ 语音唤醒对话模式已启动")
                return True
            else:
                _logger.error("❌ 启动语音唤醒对话模式失败")
                return False

        except Exception as e:
            _logger.error(f"❌ 开始语音唤醒对话时出错: {e}")
            return False

    def end_conversation(self):
        """
        结束当前对话

        Returns:
            bool: 是否成功结束对话
        """
        try:
            current_time = time.time()
            _logger.info("🛑 结束当前对话...")
            _logger.info(f"⏰ [超时测试] 对话结束时间戳: {current_time:.3f} (时间: {time.strftime('%H:%M:%S', time.localtime(current_time))})")

            # 检查当前状态
            if self.conversation_state == ConversationState.WAIT_FOR_WAKEUP:
                _logger.warning("⚠️ 当前不在对话状态")
                return False

            # 停止TTS对话模式
            if self._stop_conversation():
                self.conversation_state = ConversationState.WAIT_FOR_WAKEUP
                self.future_play_end_time = 0
                # Added: Resume music requests
                try:
                    requests.post(
                        'http://localhost:8800/resume_music', timeout=1)
                    _logger.info("✅ 已恢复音乐请求")
                except Exception as e:
                    _logger.warning(f"⚠️ 恢复失败: {e}")
                _logger.info("✅ 对话已结束，回到等待唤醒状态")
                return True
            else:
                _logger.error("❌ 停止对话模式失败")
                return False

        except Exception as e:
            _logger.error(f"❌ 结束对话时出错: {e}")
            return False

    def get_conversation_state(self) -> ConversationState:
        """
        获取当前对话状态

        Returns:
            ConversationState: 当前对话状态
        """
        return self.conversation_state

    def is_in_conversation(self) -> bool:
        """
        检查是否在对话状态中

        Returns:
            bool: 是否在对话状态中
        """
        return self.conversation_state in [ConversationState.IN_BUTTON_CONVERSATION, ConversationState.IN_WAKEUP_CONVERSATION]

    def is_waiting_for_wakeup(self) -> bool:
        """
        检查是否在等待唤醒状态

        Returns:
            bool: 是否在等待唤醒状态
        """
        return self.conversation_state == ConversationState.WAIT_FOR_WAKEUP

    def handle_button_input(self, text: str):
        """
        处理按钮触发的对话输入

        Args:
            text: 用户输入的文本

        Returns:
            bool: 是否成功处理
        """
        try:
            _logger.info(f"🔘 处理按钮输入: {text}")

            # 确保当前在按钮对话模式中
            if self.conversation_state != ConversationState.IN_BUTTON_CONVERSATION:
                _logger.info("🔄 强制切换到按钮对话模式")
                if not self.start_button_conversation():
                    _logger.error("❌ 启动按钮对话失败")
                    return False

            # 发送到AI服务器获取回复
            ai_response = self._request_server(self.dialog_ai_url, text)
            response = self._synthesize_and_play_text(ai_response)

            if response:
                duration = self._get_response_duration(response=response)
                self.future_play_end_time = time.time() + duration
                response_data = self._get_response_data(response)
                _logger.info(
                    f"🔊 按钮对话回复播放完成，回复内容: {response_data}, 时长: {duration:.2f}秒")
                return True
            else:
                self.future_play_end_time = 0
                _logger.error("❌ 按钮对话回复播放失败")
                return False

        except Exception as e:
            _logger.error(f"❌ 处理按钮输入时出错: {e}")
            return False

    def stop_button_conversation(self):
        """
        处理按钮松开事件，强制结束当前对话

        Returns:
            bool: 是否成功结束对话
        """
        try:
            _logger.info("🔘 处理按钮松开事件...")

            # 检查当前状态
            if self.conversation_state == ConversationState.IN_BUTTON_CONVERSATION:
                _logger.info("✅ 按钮松开，结束按钮对话")
                self._synthesize_and_play_text("按钮对话結束")
                return self.end_conversation()
            elif self.conversation_state == ConversationState.IN_WAKEUP_CONVERSATION:
                _logger.info("✅ 按钮松开，强制结束语音唤醒对话")
                self._synthesize_and_play_text("语音对话结束")
                return self.end_conversation()
            elif self.conversation_state == ConversationState.WAIT_FOR_WAKEUP:
                _logger.warning("⚠️ 当前不在对话状态，按钮松开无效果")
                return False
            else:
                _logger.warning(f"⚠️ 未知状态: {self.conversation_state}")
                return False

        except Exception as e:
            _logger.error(f"❌ 处理按钮松开事件时出错: {e}")
            return False

    def test_create_temp_file(self):
        """
        测试创建临时文件的功能
        """
        try:
            _logger.info("🧪 开始测试临时文件创建...")

            # 创建一个测试音频数据
            import numpy as np
            test_audio = np.random.rand(16000) * 0.1  # 1秒的随机音频数据

            # 创建临时文件
            temp_file = self.save_audio_to_temp_file(test_audio)

            if temp_file and os.path.exists(temp_file):
                _logger.info(f"✅ 测试成功！临时文件已创建: {temp_file}")
                _logger.info(f"   文件大小: {os.path.getsize(temp_file)} 字节")
                return temp_file
            else:
                _logger.error("❌ 测试失败！临时文件创建失败")
                return None

        except Exception as e:
            _logger.error(f"❌ 测试过程中出错: {e}")
            return None

    def conversation_stream_loop(self):
        """
        演示音频流功能
        """
        _logger.info("🎵 音频流功能演示")
        _logger.info("="*50)
        _logger.info("功能说明:")
        _logger.info("  - 持续读取音频chunk并存入两个队列")
        _logger.info("  - VAD队列用于语音活动检测")
        _logger.info("  - 其他队列可用于其他音频处理")
        _logger.info("  - VAD处理在新线程中运行")
        _logger.info("  - 按 Ctrl+C 退出演示")
        _logger.info("="*50)

        try:
            # 启动音频流
            self.start_audio_stream()

            _logger.info("🎙️ 音频流已启动，开始监听...")
            _logger.info("💡 请说话测试VAD功能")

            # 主循环：显示队列状态
            
            while True:
                time.sleep(2)  # 每2秒显示一次状态
                
                # 从数据库读取配置参数
                threshold = get_para_value("hmi.silence_duration_threshold")
                if threshold is not None:
                    self.silence_duration_threshold = int(threshold)
                    _logger.info(
                        f"silence_duration_threshold: {self.silence_duration_threshold}")

                min_energy = get_para_value("hmi.detect_dialog_min_energy")
                if min_energy is not None:
                    self.min_speech_energy = float(min_energy)
                    _logger.info(
                        f"min_speech_energy: {self.min_speech_energy}")

                asr_server_url = get_para_value("hmi.asr_server_url")
                if asr_server_url is not None:
                    self.asr_server_url = asr_server_url
                    _logger.info(f"asr_server_url: {self.asr_server_url}")

                nick_name = get_para_value("main.nickname")
                if nick_name is not None:
                    self.nick_name = nick_name
                    _logger.info(f"main.nickname: {self.nick_name}")

                dialog_ai_url = get_para_value("hmi.dialog_ai_url")
                if dialog_ai_url is not None:
                    self.dialog_ai_url = dialog_ai_url
                    _logger.info(f"dialog_ai_url: {self.dialog_ai_url}")
                #  hmi.speaker_volume
                speaker_volume = get_para_value("hmi.speaker_volume")
                if speaker_volume is not None:
                    volume = float(speaker_volume)/100.0
                    self.tts_data.set_volume(volume)
                
                status = self.get_queue_status()
                # _logger.info(f"📊 队列状态: VAD队列={status['vad_queue_size']}, "
                #           f"VAD处理中={status['vad_processing']}")
        except KeyboardInterrupt:
            _logger.info("\n\n👋 用户中断，停止音频流...")
        except Exception as e:
            _logger.error(f"\n❌ 音频流演示出错: {e}")
        finally:
            # 停止音频流
            self.stop_audio_stream()
            _logger.info("✅ 音频流演示结束")


if __name__ == "__main__":
    # 选择运行模式
    import sys
    reader = AudioStreamReader()
    reader.conversation_stream_loop()
