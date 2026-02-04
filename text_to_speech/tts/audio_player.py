# Copyright 2025 Standard Robots Co. All rights reserved.

"""
语音播放服务流程图
================

系统状态流转图：
┌─────────────────┐
│   音乐模式      │ ←─── 默认启动状态
│  (Music Mode)   │
└─────────────────┘
         │
         │ 接收 START_CONVERSATION 指令
         ▼
┌─────────────────┐
│   对话模式      │
│(Conversation    │
│     Mode)       │
└─────────────────┘
         │
         │ 接收 STOP_CONVERSATION 指令
         ▼
┌─────────────────┐
│   音乐模式      │
│  (Music Mode)   │
└─────────────────┘

指令处理逻辑：
┌─────────────────────────────────────────────────────────────┐
│ 音乐模式下的指令处理：                                        │
│ ├── PLAY_MUSIC: 播放音乐内容                                 │
│ ├── PLAY_TEXT: 播放文本内容                                  │
│ └── START_CONVERSATION: 切换到对话模式                       │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ 对话模式下的指令处理：                                        │
│ ├── PLAY_MUSIC: 忽略（不生效）                               │
│ ├── PLAY_TEXT: 忽略（不生效）                                │
│ └── STOP_CONVERSATION: 结束对话模式，返回音乐模式             │
└─────────────────────────────────────────────────────────────┘

状态切换规则：
1. 系统默认启动时进入音乐模式
2. 音乐模式下，PLAY_MUSIC 和 PLAY_TEXT 指令正常执行
3. 音乐模式下，START_CONVERSATION 指令切换到对话模式
4. 对话模式下，所有音乐相关指令（PLAY_MUSIC、PLAY_TEXT）被忽略
5. 对话模式下，只有 STOP_CONVERSATION 指令有效，用于返回音乐模式
6. 对话模式期间，系统专注于对话交互，不响应音乐播放请求
"""

# 移除对本地TTS的导入，改为使用远程HTTP服务
# from tts.standard_tts import TTSApp
import requests
import json
import log_config
import subprocess
import threading
import asyncio
import queue
import time
from typing import List, Optional  # noqa: UP035, UP006
from enum import Enum
from dataclasses import dataclass
from pathlib import Path
from contextlib import asynccontextmanager
from pydantic import BaseModel
import tempfile
import wave
import numpy as np
import os
import sys
import os.path
import logging

# 获取项目根目录
project_root = Path(__file__).parent.parent
# 添加项目根目录到Python路径
sys.path.insert(0, str(project_root))

# 导入统一的日志配置

# 现在可以直接导入根目录的模块

# 配置日志
logger = logging.getLogger(__name__)

# 导入数据库配置读取函数（复用dialog模块中的函数）
try:
    from dialog.dialog_recognize import get_para_value
except ImportError:
    # 如果导入失败，定义备用函数
    def get_para_value(key: str):
        logger.warning(f"无法导入get_para_value，使用默认值")
        return None

# TTS服务配置
class TTSServiceConfig:
    """远程TTS服务配置"""
    def __init__(self):
        # 优先从数据库读取配置，如果数据库没有则使用环境变量或默认值
        self._load_config()
    
    def _load_config(self):
        """加载配置：优先从数据库读取，否则使用环境变量或默认值"""
        # 从数据库读取TTS服务URL
        db_url = get_para_value("hmi.tts_service_url")
        if db_url:
            self.service_url = db_url
            logger.info(f"从数据库读取TTS服务URL: {self.service_url}")
        else:
            # 从环境变量或默认值读取
            self.service_url = os.getenv('TTS_SERVICE_URL', 'http://10.10.68.49:5000/tts')
            logger.info(f"使用环境变量/默认值TTS服务URL: {self.service_url}")
        
        # 从数据库读取超时配置
        db_timeout = get_para_value("hmi.tts_timeout")
        if db_timeout:
            self.timeout = int(db_timeout)
            logger.info(f"从数据库读取TTS超时: {self.timeout}")
        else:
            # 从环境变量或默认值读取
            self.timeout = int(os.getenv('TTS_TIMEOUT', '30'))
            logger.info(f"使用环境变量/默认值TTS超时: {self.timeout}")
    
    def reload_config(self):
        """重新加载配置（用于运行时更新）"""
        self._load_config()
    
    def get_synthesize_url(self):
        """获取语音合成接口URL"""
        return f"{self.service_url}"

# 全局变量


class PlayMode(str, Enum):
    ADD = 'add'  # 添加到播放列表
    REPLACE = 'replace'  # 替换当前播放
    STOP = 'stop'  # 停止播放
    CLEAR = 'clear'  # 清空播放队列


class StatusCommand(str, Enum):
    START_CONVERSATION = 'start_conversation'  # 开始对话模式
    STOP_CONVERSATION = 'stop_conversation'  # 结束对话模式，返回音乐模式
    PLAY_MUSIC = 'play_music'  # 播放音乐（仅在音乐模式下生效）
    PLAY_CONVERSATION = 'play_conversation'  # 播放对话内容
    PLAY_TEXT = 'play_text'  # 播放文本（仅在音乐模式下生效）


class PlayRequest(BaseModel):
    file_path: str = ''  # 语音文件路径
    music_text: str = ''  # 音乐文本
    play_interval: float = 1.0  # 播放间隔（秒）
    play_count: int = 1  # 播放次数
    replay_interval: float = 10.0  # 重播间隔（秒）
    priority: int = 1  # 优先级（数字越小优先级越高）
    mode: PlayMode = PlayMode.ADD  # 播放模式
    status_command: StatusCommand = StatusCommand.PLAY_TEXT  # 状态命令
    volume: float = 1.0  # 播放音量 (0.0-1.0)


@dataclass
class PlayTask:
    file_path: str
    music_text: str
    play_interval: float
    play_count: int
    priority: int
    volume: float
    created_time: float
    duration: float = 0.0  # 预估播放时长（秒）


class SystemStateManager:
    """系统状态管理器 - 管理音乐模式和对话模式的切换"""

    def __init__(self):
        self.is_playing_now = False
        self.last_play_end_time = 0
        self.last_play_end_music_time = 0
        self.is_music_playing_now = False
        self._current_mode = 'music'  # 默认音乐模式
        self._lock = threading.Lock()
        self._volume = -1.0
        self._music_paused = False

    @property
    def current_mode(self) -> str:
        """获取当前模式"""
        with self._lock:
            return self._current_mode

    def is_music_mode(self) -> bool:
        """检查是否处于音乐模式"""
        return self.current_mode == 'music'

    def is_conversation_mode(self) -> bool:
        """检查是否处于对话模式"""
        return self.current_mode == 'conversation'

    def switch_to_conversation_mode(self) -> bool:
        """切换到对话模式"""
        with self._lock:
            if self._current_mode == 'music':
                self._current_mode = 'conversation'
                self._music_paused = True
                logger.info('系统已切换到对话模式，音乐请求暂停')
                logger.info('系统已切换到对话模式')
                return True
            return self._current_mode == 'conversation'

    def switch_to_music_mode(self) -> bool:
        """切换到音乐模式"""
        with self._lock:
            if self._current_mode == 'conversation':
                self._current_mode = 'music'
                self._music_paused = False
                logger.info('系统已切换回音乐模式，恢复音乐请求')
                logger.info('系统已切换到音乐模式')
                return True
            return self._current_mode == 'music'

    def can_process_command(self, command: StatusCommand) -> bool:
        """检查当前模式下是否可以处理指定命令"""
        if command == StatusCommand.START_CONVERSATION:
            return self.is_music_mode()  # 只有在音乐模式下才能开始对话
        elif command == StatusCommand.STOP_CONVERSATION:
            return self.is_conversation_mode()  # 只有在对话模式下才能结束对话
        elif command in [StatusCommand.PLAY_MUSIC, StatusCommand.PLAY_TEXT]:
            return self.is_music_mode()  # 音乐相关命令只在音乐模式下生效
        elif command == StatusCommand.PLAY_CONVERSATION:
            return self.is_conversation_mode()  # 对话命令只在对话模式下生效
        return False


class AudioPlayer:
    def __init__(self):
        self.current_process: Optional[subprocess.Popen] = None
        self.play_queue: List[PlayTask] = []  # noqa: UP006
        self.is_playing = False
        self.lock = asyncio.Lock()
        self.sync_lock = threading.Lock()  # 添加同步锁
        # 移除本地TTS应用，改为使用远程服务
        # self.tts_app = TTSApp()
        self.tts_config = TTSServiceConfig()  # 远程TTS服务配置
        self.executor = None
        self._volume = -1.0  # 初始化音量属性

    async def add_to_queue(self, task: PlayTask):
        """添加播放任务到队列"""
        async with self.lock:
            self.play_queue.append(task)
            # 按优先级排序
            self.play_queue.sort(key=lambda x: (x.priority, x.created_time))

    async def replace_queue(self, task: PlayTask):
        """替换当前播放队列"""
        async with self.lock:
            self.play_queue.clear()
            self.play_queue.append(task)

    def add_to_queue_sync(self, task: PlayTask):
        """同步添加播放任务到队列"""
        with self.sync_lock:
            self.play_queue.append(task)
            # 按优先级排序
            self.play_queue.sort(key=lambda x: (x.priority, x.created_time))

    def replace_queue_sync(self, task: PlayTask):
        """同步替换当前播放队列"""
        with self.sync_lock:
            self.play_queue.clear()
            self.play_queue.append(task)

    def stop(self):
        self.play_queue.clear()
        self.stop_current()

    def stop_current(self):
        """停止当前播放"""
        subprocess.run(['pkill', '-f', 'aplay'], check=False)
        if self.current_process and self.current_process.poll() is None:
            try:
                # 使用aplay -t 终止播放
                logger.info("terminate")
                self.current_process.terminate()
                self.current_process.wait(timeout=2)
                logger.info("terminate success")
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                self.current_process.kill()
            except Exception as e:
                logger.error(f"停止播放失败: {e}")
                self.current_process = None
            finally:
                self.current_process = None

    def set_volume(self, volume: float):
        if volume <= 0.0:
            return
        if self._volume != volume:
            self._volume = volume
            # 使用amixer设置音量，然后播放
            volume_percent = int(volume * 100)
            logger.info(f"set volume to {volume_percent}%")
            subprocess.run(["amixer", "set", "'DAC VOLUME'",
                           f"{volume_percent}%"], check=False)

    def _read_wav_file_pure_numpy(self, file_path: str):
        """
        使用纯 numpy 读取 WAV 文件
        
        Args:
            file_path: WAV 文件路径
            
        Returns:
            tuple: (sample_rate, audio_data)
                - sample_rate: 采样率 (int)
                - audio_data: 音频数据 (numpy.ndarray)
                
        Raises:
            RuntimeError: 文件读取失败或格式不支持
        """
        try:
            with open(file_path, 'rb') as f:
                # 读取 WAV 文件头（44字节标准头）
                riff = f.read(4)  # "RIFF"
                if riff != b'RIFF':
                    raise RuntimeError(f"不是有效的 WAV 文件: {file_path}")
                
                file_size = int.from_bytes(f.read(4), 'little')
                wave_tag = f.read(4)  # "WAVE"
                
                if wave_tag != b'WAVE':
                    raise RuntimeError(f"不是有效的 WAVE 格式: {file_path}")
                
                # 读取 fmt 子块
                fmt_tag = f.read(4)  # "fmt "
                fmt_size = int.from_bytes(f.read(4), 'little')
                audio_format = int.from_bytes(f.read(2), 'little')  # 1 = PCM
                num_channels = int.from_bytes(f.read(2), 'little')
                sample_rate = int.from_bytes(f.read(4), 'little')
                byte_rate = int.from_bytes(f.read(4), 'little')
                block_align = int.from_bytes(f.read(2), 'little')
                bits_per_sample = int.from_bytes(f.read(2), 'little')
                
                # 跳过可能的额外 fmt 数据
                if fmt_size > 16:
                    f.read(fmt_size - 16)
                
                # 查找 data 子块
                while True:
                    chunk_id = f.read(4)
                    if not chunk_id:
                        raise RuntimeError("未找到 data 块")
                    chunk_size = int.from_bytes(f.read(4), 'little')
                    
                    if chunk_id == b'data':
                        # 找到数据块
                        break
                    else:
                        # 跳过其他块
                        f.read(chunk_size)
                
                # 读取音频数据
                audio_bytes = f.read(chunk_size)
                
                # 根据位深度转换为 numpy 数组
                if bits_per_sample == 16:
                    audio_data = np.frombuffer(audio_bytes, dtype=np.int16)
                    max_val = 32768.0  # 2^15
                elif bits_per_sample == 24:
                    # 24位需要特殊处理
                    audio_data = np.frombuffer(audio_bytes, dtype=np.uint8)
                    audio_data = audio_data.reshape(-1, 3)
                    # 转换为 int32
                    audio_data = np.pad(audio_data, ((0, 0), (0, 1)), mode='constant')
                    audio_data = audio_data.view(np.int32).flatten()
                    audio_data = audio_data >> 8  # 右移8位
                    max_val = 8388608.0  # 2^23
                elif bits_per_sample == 32:
                    audio_data = np.frombuffer(audio_bytes, dtype=np.int32)
                    max_val = 2147483648.0  # 2^31
                else:
                    raise RuntimeError(f"不支持的位深度: {bits_per_sample}")
                
                # 转换为 float32，归一化到 [-1.0, 1.0]
                audio_float = audio_data.astype(np.float32) / max_val
                
                # 处理多声道（转为 (samples, channels) 形状）
                if num_channels > 1:
                    audio_float = audio_float.reshape(-1, num_channels)
                
                logger.info(f"📖 读取 WAV 文件: {file_path}")
                logger.info(f"   采样率: {sample_rate} Hz")
                logger.info(f"   声道数: {num_channels}")
                logger.info(f"   位深度: {bits_per_sample} bit")
                logger.info(f"   样本数: {len(audio_float)}")
                
                return sample_rate, audio_float, num_channels, bits_per_sample
                
        except Exception as e:
            raise RuntimeError(f"读取 WAV 文件失败: {e}")
    
    def _write_wav_file_pure_numpy(self, file_path: str, sample_rate: int, audio_data: np.ndarray, bits_per_sample: int = 16):
        """
        使用纯 numpy 写入 WAV 文件
        
        Args:
            file_path: 输出文件路径
            sample_rate: 采样率
            audio_data: 音频数据 (float32, 范围 [-1.0, 1.0])
            bits_per_sample: 位深度 (16 或 32)
        """
        try:
            # 确定声道数
            if len(audio_data.shape) == 1:
                num_channels = 1
                samples = len(audio_data)
            else:
                num_channels = audio_data.shape[1]
                samples = audio_data.shape[0]
            
            # 转换为整数格式
            if bits_per_sample == 16:
                max_val = 32767.0
                audio_int = np.clip(audio_data * max_val, -32768, 32767).astype(np.int16)
                bytes_per_sample = 2
            elif bits_per_sample == 32:
                max_val = 2147483647.0
                audio_int = np.clip(audio_data * max_val, -2147483648, 2147483647).astype(np.int32)
                bytes_per_sample = 4
            else:
                raise RuntimeError(f"不支持的位深度: {bits_per_sample}")
            
            # 展平多声道数据
            if len(audio_int.shape) > 1:
                audio_int = audio_int.flatten()
            
            # 计算文件大小
            byte_rate = sample_rate * num_channels * bytes_per_sample
            block_align = num_channels * bytes_per_sample
            data_size = len(audio_int.tobytes())
            file_size = 36 + data_size
            
            with open(file_path, 'wb') as f:
                # RIFF 头
                f.write(b'RIFF')
                f.write(file_size.to_bytes(4, 'little'))
                f.write(b'WAVE')
                
                # fmt 子块
                f.write(b'fmt ')
                f.write((16).to_bytes(4, 'little'))  # fmt 块大小
                f.write((1).to_bytes(2, 'little'))   # 音频格式 (PCM)
                f.write(num_channels.to_bytes(2, 'little'))
                f.write(sample_rate.to_bytes(4, 'little'))
                f.write(byte_rate.to_bytes(4, 'little'))
                f.write(block_align.to_bytes(2, 'little'))
                f.write(bits_per_sample.to_bytes(2, 'little'))
                
                # data 子块
                f.write(b'data')
                f.write(data_size.to_bytes(4, 'little'))
                f.write(audio_int.tobytes())
            
            logger.info(f"💾 写入 WAV 文件: {file_path}")
            logger.info(f"   文件大小: {os.path.getsize(file_path)} bytes")
            
        except Exception as e:
            raise RuntimeError(f"写入 WAV 文件失败: {e}")
    
    def _normalize_audio_with_numpy(self, input_file: str, target_rms_db: float = -20.0, peak_db: float = -3.0) -> str:
        """
        使用纯 numpy 标准化音频文件音量（替代 sox）
        
        算法说明:
        1. RMS 标准化: 调整音频的均方根音量到目标值
        2. 峰值限制: 防止削波失真
        
        Args:
            input_file: 输入音频文件路径
            target_rms_db: 目标 RMS 音量（dB），默认 -20dB
            peak_db: 峰值限制（dB），默认 -3dB
            
        Returns:
            str: 标准化后的临时文件路径
            
        Raises:
            RuntimeError: 处理失败时抛出
        """
        import tempfile
        import os
        
        temp_path = None
        try:
            # 1. 读取原始音频文件
            sample_rate, audio_data, num_channels, bits_per_sample = self._read_wav_file_pure_numpy(input_file)
            
            logger.info(f"🔧 使用 numpy 标准化音频: {input_file}")
            logger.info(f"   目标 RMS: {target_rms_db} dB")
            logger.info(f"   峰值限制: {peak_db} dB")
            
            # 2. 计算当前 RMS（均方根）
            # RMS = sqrt(mean(samples^2))
            current_rms = np.sqrt(np.mean(audio_data ** 2))
            
            if current_rms < 1e-10:  # 静音检测
                logger.warning("⚠️ 检测到静音音频，跳过标准化")
                # 直接复制原文件
                temp_fd, temp_path = tempfile.mkstemp(suffix='.wav', prefix='normalized_')
                os.close(temp_fd)
                import shutil
                shutil.copy2(input_file, temp_path)
                return temp_path
            
            # 3. 计算目标 RMS（从 dB 转换为线性值）
            # dB = 20 * log10(amplitude)
            # amplitude = 10^(dB/20)
            target_rms_linear = 10 ** (target_rms_db / 20.0)
            
            # 4. 计算增益因子
            gain = target_rms_linear / current_rms
            
            logger.info(f"   当前 RMS: {current_rms:.6f} ({20 * np.log10(current_rms):.2f} dB)")
            logger.info(f"   目标 RMS: {target_rms_linear:.6f} ({target_rms_db:.2f} dB)")
            logger.info(f"   增益因子: {gain:.4f} ({20 * np.log10(gain):.2f} dB)")
            
            # 5. 应用增益
            normalized_audio = audio_data * gain
            
            # 6. 峰值限制（防止削波）
            peak_linear = 10 ** (peak_db / 20.0)
            current_peak = np.max(np.abs(normalized_audio))
            
            if current_peak > peak_linear:
                # 需要限制峰值
                peak_gain = peak_linear / current_peak
                normalized_audio = normalized_audio * peak_gain
                logger.info(f"   峰值限制: {current_peak:.4f} -> {peak_linear:.4f} (降低 {20 * np.log10(peak_gain):.2f} dB)")
            else:
                logger.info(f"   峰值正常: {current_peak:.4f} < {peak_linear:.4f}")
            
            # 7. 最终削波保护（硬限制在 [-1.0, 1.0]）
            normalized_audio = np.clip(normalized_audio, -1.0, 1.0)
            
            # 8. 创建临时文件
            temp_fd, temp_path = tempfile.mkstemp(suffix='.wav', prefix='normalized_')
            os.close(temp_fd)
            
            # 9. 写入标准化后的音频
            self._write_wav_file_pure_numpy(temp_path, sample_rate, normalized_audio, bits_per_sample)
            
            # 10. 验证输出文件
            if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
                raise RuntimeError("标准化输出文件为空或不存在")
            
            logger.info(f"✅ numpy 标准化完成: {os.path.getsize(temp_path)} bytes")
            return temp_path
            
        except Exception as e:
            # 清理临时文件
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except:
                    pass
            raise RuntimeError(f"numpy 标准化失败: {e}")

    def _cleanup_temp_file(self, file_path: str):
        """清理临时文件"""
        try:
            if os.path.exists(file_path):
                os.unlink(file_path)
                logger.debug(f"🗑️ 清理临时文件: {file_path}")
        except Exception as e:
            logger.warning(f"清理临时文件失败: {e}")

    def play_file(self, file_path: str, volume: float = 1.0):
        """
        播放单个文件（使用 numpy 标准化音量）
        
        Args:
            file_path: 音频文件路径
            volume: 音量倍数（0.0-1.0），默认1.0
            
        Returns:
            bool: 播放是否成功启动
        """
        if not Path(file_path).exists():
            raise FileNotFoundError(f'文件不存在: {file_path}')

        normalized_file = None
        try:
            # 使用 numpy 标准化音频文件
            logger.info(f"🎵 开始播放文件: {file_path}")
            normalized_file = self._normalize_audio_with_numpy(file_path)
            
            # 构建aplay命令
            cmd = ['aplay', '-q']
            self.set_volume(volume)
            cmd.append(normalized_file)
            
            logger.info(f"🔊 播放标准化音频: {normalized_file}")
            
            # 使用非阻塞方式启动播放进程
            self.current_process = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            if self.current_process:
                self.current_process.wait()
                logger.info(f"✅ 播放完成: {file_path}")
                return True  # 播放已启动
            return False  # 播放失败
            
        except Exception as e:
            logger.error(f"❌ 播放失败: {e}")
            # 如果 numpy 标准化失败，尝试直接播放原文件
            if normalized_file:
                self._cleanup_temp_file(normalized_file)
                normalized_file = None
            
            logger.warning(f"⚠️ numpy 标准化失败，尝试直接播放原文件: {file_path}")
            try:
                cmd = ['aplay', '-q']
                self.set_volume(volume)
                cmd.append(file_path)
                
                self.current_process = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                if self.current_process:
                    self.current_process.wait()
                    logger.info(f"✅ 直接播放完成: {file_path}")
                    return True
                return False
            except subprocess.SubprocessError as fallback_e:
                raise RuntimeError(f'播放失败: {e} (降级播放也失败: {fallback_e})') from e
        finally:
            # 清理临时文件
            if normalized_file:
                self._cleanup_temp_file(normalized_file)

    def split_text_by_words(self, text: str, max_length: int = 10) -> List[str]:
        import re

        """按词语分割文本，确保每段字数适中且词语完整"""
        # 中文标点符号
        chinese_punctuation = r'[，。！？；：""' '（）【】《》、]'
        # 英文标点符号
        english_punctuation = r'[,.!?;:"\'()\[\]<>/]'

        def merge_punctuation(chinese, english):
            # 提取字符类中的内容（去掉方括号）
            chinese_chars = chinese.strip('[]')
            english_chars = english.strip('[]')
            # 合并并放入新的字符类
            return f'[{chinese_chars}{english_chars}]'

        punctuation = merge_punctuation(
            chinese_punctuation, english_punctuation)

        # 按标点符号分割文本
        segments = re.split(f'({punctuation})', text)
        logger.info(f'文本已分为 {len(segments)} 段')
        logger.info(segments)
        # 重新组合，保持标点符号
        combined_segments = []
        current_segment = ''

        for i in range(0, len(segments), 2):
            segment = segments[i]
            punctuation_mark = segments[i + 1] if i + 1 < len(segments) else ''

            # 如果当前段落加上新内容超过最大长度，先保存当前段落
            if len(current_segment + segment + punctuation_mark) > max_length and current_segment:
                combined_segments.append(current_segment)
                current_segment = ''

            current_segment += segment + punctuation_mark

        # 添加最后一个段落
        if current_segment:
            combined_segments.append(current_segment)

        # 过滤空段落
        combined_segments = [seg.strip()
                             for seg in combined_segments if seg.strip()]

        return combined_segments

    def text_to_speech(self, text: str):
        """将文本转换为语音文件 - 使用远程TTS服务（流式接收多个音频文件）"""
        try:
            logger.info(f'开始远程文本转语音: {text}')
            
            # 调用远程TTS服务
            response = requests.post(
                self.tts_config.get_synthesize_url(),
                json={'text': text},
                timeout=self.tts_config.timeout,
                stream=True  # 启用流式接收
            )
            
            if response.status_code == 200:
                # 解析流式响应
                buffer = b""
                segment_count = 0
                
                for chunk in response.iter_content(chunk_size=8192):
                    buffer += chunk
                    
                    # 查找音频片段分隔符
                    while b"--AUDIO_SEGMENT_" in buffer:
                        # 找到分隔符位置
                        separator_start = buffer.find(b"--AUDIO_SEGMENT_")
                        if separator_start == -1:
                            break
                            
                        # 找到分隔符结束位置
                        separator_end = buffer.find(b"\r\n", separator_start)
                        if separator_end == -1:
                            break
                            
                        # 找到Content-Length头
                        length_start = separator_end + 2
                        length_end = buffer.find(b"\r\n\r\n", length_start)
                        if length_end == -1:
                            break
                            
                        # 解析Content-Length
                        length_header = buffer[length_start:length_end].decode()
                        if not length_header.startswith("Content-Length: "):
                            break
                            
                        content_length = int(length_header.split(": ")[1])
                        audio_start = length_end + 4
                        audio_end = audio_start + content_length
                        
                        # 检查是否有完整的音频数据
                        if len(buffer) < audio_end + 2:  # +2 for \r\n
                            break
                            
                        # 提取音频数据
                        audio_data = buffer[audio_start:audio_end]
                        
                        segment_count += 1
                        logger.info(f'接收到第{segment_count}个音频片段: {len(audio_data)} 字节')
                        # 直接返回WAV音频数据
                        yield audio_data
                        
                        # 移除已处理的数据
                        buffer = buffer[audio_end + 2:]
                        
                logger.info(f'文本转语音完成，生成了{segment_count}个音频文件')
                
            else:
                logger.error(f'TTS服务请求失败: HTTP {response.status_code}')
                return None

        except Exception as e:
            logger.error(f'远程文本转语音失败: {e}')
            return None

    def set_play_speed_and_volume(self, audio_file, playback_speed: float = 0.95, volume: float = 8.0):
        # 放大音频向量以提高音量
        audio_amplified = audio_file * volume

        # 限制在[-1, 1]范围内，避免削波
        audio_amplified = np.clip(audio_amplified, -1.0, 1.0)
        # 通过重采样来改变播放速度
        if playback_speed != 1.0:
            # 计算新的采样点数
            original_length = len(audio_amplified)
            new_length = int(original_length / playback_speed)

            # 使用线性插值进行重采样
            original_indices = np.arange(original_length)
            new_indices = np.linspace(
                0, original_length - 1, new_length)
            # 线性插值
            audio_amplified = np.interp(
                new_indices, original_indices, audio_amplified)
        return audio_amplified

    def _generate_audio_worker(self, text_segments, volume, audio_queue, stop_event):
        """音频生成工作线程"""
        try:
            for i, segment in enumerate(text_segments):
                if stop_event.is_set():
                    break

                logger.info(
                    f'正在生成第 {i + 1}/{len(text_segments)} 段音频: {segment}')

                # 生成音频
                # for audio_file in self.text_to_speech(segment, 5.0):
                #     if stop_event.is_set():
                #         break
                #     # 处理音频文件
                #     audio_file_name = self._process_audio_file(
                #         audio_file, volume)
                for audio_data in self.text_to_speech(segment):
                    if audio_data:
                        # 将音频数据保存为临时文件
                        temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
                        temp_file.write(audio_data)
                        temp_file.close()
                        
                        logger.info(f'音频文件已生成: {temp_file.name}')
                        audio_queue.put((i, temp_file.name))
            # 发送结束信号
            audio_queue.put((None, None))

        except Exception as e:
            logger.error(f'音频生成线程发生错误: {e}')
            audio_queue.put((None, None))

    def _play_audio_worker(self, text_segments, volume, audio_queue, stop_event):
        """音频播放工作线程"""
        try:
            current_segment = 0
            pending_audio = {}

            while True:
                try:
                    # 获取音频文件，设置超时避免无限等待
                    segment_idx, audio_file_name = audio_queue.get(timeout=1.0)

                    if segment_idx is None:  # 结束信号
                        # 播放剩余的音频文件
                        for segment_audio_list in pending_audio.values():
                            for audio_file in segment_audio_list:
                                self._play_audio_file_sync(audio_file, volume)
                        break

                    # 将音频文件添加到对应段的列表
                    if segment_idx not in pending_audio:
                        pending_audio[segment_idx] = []
                    pending_audio[segment_idx].append(audio_file_name)

                    # 如果当前段的所有音频都已生成，开始播放
                    if segment_idx == current_segment:
                        # 播放当前段的所有音频
                        if current_segment in pending_audio:
                            for audio_file in pending_audio[current_segment]:
                                self._play_audio_file_sync(audio_file, volume)
                            del pending_audio[current_segment]

                        current_segment += 1

                        # 检查是否可以播放下一段
                        while current_segment in pending_audio:
                            logger.info(f'播放第 {current_segment + 1} 段音频')
                            for audio_file in pending_audio[current_segment]:
                                self._play_audio_file_sync(audio_file, volume)
                            del pending_audio[current_segment]
                            current_segment += 1

                        # 如果不是最后一段，继续等待
                        if current_segment < len(text_segments):
                            logger.info(f'等待第 {current_segment + 1} 段音频生成...')

                except queue.Empty:
                    # 超时，检查是否需要停止
                    if stop_event.is_set():
                        break
                    continue
                except Exception as e:
                    logger.error(f'播放音频线程发生错误: {e}')
                    break

        except Exception as e:
            logger.error(f'音频播放线程发生错误: {e}')

    def play_text(self, text: str, volume: float = 1.0):
        """播放文本内容，支持分段播放和边播放边生成"""
        if not text or not text.strip():
            return

        try:
            self.set_volume(volume)
            # 分段文本
            text_segments = self.split_text_by_words(text)
            logger.info(f'文本将分为 {len(text_segments)} 段播放')

            # 创建音频队列
            audio_queue = queue.Queue()
            stop_event = threading.Event()

            # 启动音频生成线程 (Producer)
            gen_thread = threading.Thread(
                target=self._generate_audio_worker, 
                args=(text_segments, volume, audio_queue, stop_event),
                daemon=True
            )
            gen_thread.start()

            # 启动音频播放线程 (Consumer)
            play_thread = threading.Thread(
                target=self._play_audio_worker,
                args=(text_segments, volume, audio_queue, stop_event),
                daemon=True
            )
            play_thread.start()

            logger.info('文本播放任务已转入后台并行处理')

        except Exception as e:
            logger.error(f'启动播放文本任务失败: {e}')
            raise RuntimeError(f'播放文本失败: {e}') from e

    def _play_audio_file_sync(self, audio_file_name, volume):
        """同步播放音频文件并确保清理"""
        try:
            # 增加物理文件就绪检查，应对高并发下的 OS 写入延迟
            max_checks = 5
            for i in range(max_checks):
                if os.path.exists(audio_file_name) and os.path.getsize(audio_file_name) > 0:
                    break
                logger.debug(f"⏳ 等待音频文件就绪 ({i+1}/{max_checks}): {audio_file_name}")
                time.sleep(0.05)
            else:
                logger.error(f"❌ 播放终止：音频文件未就绪: {audio_file_name}")
                return

            logger.info(f'正在播放音频片段: {audio_file_name}')
            # play_file 内部是阻塞的（调用了 current_process.wait()）
            success = self.play_file(audio_file_name, volume)

            # 播放结束后清理临时文件
            try:
                if os.path.exists(audio_file_name):
                    os.unlink(audio_file_name)
                    logger.info(f'已清理临时音频: {audio_file_name}')
            except Exception as cleanup_e:
                logger.warning(f'清理临时音频失败: {cleanup_e}')

        except Exception as e:
            logger.error(f'播放音频文件时发生异常: {e}')

    def _play_text_sequential(self, text_segments, volume):
        """串行播放文本（回退方案）"""
        logger.info('使用串行播放模式')
        for i, segment in enumerate(text_segments):
            logger.info(f'正在处理第 {i + 1}/{len(text_segments)} 段: {segment}')

            # 转换为语音
            for audio_file in self.text_to_speech(segment, volume):
                temp_audio_file = tempfile.NamedTemporaryFile(
                    suffix='.wav', delete=False)
                sample_rate = 22050
                audio_amplified = self.set_play_speed_and_volume(
                    audio_file, playback_speed=0.95, volume=8.0)

                audio_int16 = (audio_amplified * 32767).astype(np.int16)
                with wave.open(temp_audio_file, 'w') as wav_file:
                    wav_file.setnchannels(1)  # 单声道
                    wav_file.setsampwidth(2)  # 16位
                    wav_file.setframerate(sample_rate)
                    wav_file.writeframes(audio_int16.tobytes())
                audio_file_name = temp_audio_file.name
                temp_audio_file.close()

                # 播放音频
                logger.info(f'播放音频: {audio_file_name}')
                success = self.play_file(audio_file_name, volume)
                if not success:
                    logger.warning(f'第 {i + 1} 段播放失败')

                # 清理临时文件
                try:
                    import os
                    os.unlink(audio_file_name)
                except Exception as e:
                    logger.error(f'清理临时文件失败: {e}')

    async def clear_queue(self):
        """清空播放队列"""
        async with self.lock:
            self.play_queue.clear()
            self.is_playing = False

    async def get_queue_status(self):
        """获取队列状态"""
        async with self.lock:
            current_file = None
            if self.current_process and hasattr(self.current_process, 'args') and self.current_process.args:
                current_file = self.current_process.args[-1]

            return {
                'is_playing': self.is_playing,
                'queue_length': len(self.play_queue),
                'current_file': current_file,
            }

    def clear_queue_sync(self):
        """同步清空播放队列"""
        with self.sync_lock:
            self.play_queue.clear()
            self.is_playing = False

    def get_queue_status_sync(self):
        """同步获取队列状态"""
        with self.sync_lock:
            current_file = None
            if self.current_process and hasattr(self.current_process, 'args') and self.current_process.args:
                current_file = self.current_process.args[-1]

            return {
                'is_playing': self.is_playing,
                'queue_length': len(self.play_queue),
                'current_file': current_file,
            }

    def get_audio_file_duration(self, file_path: str) -> float:
        """获取音频文件时长（秒）"""
        try:
            if not os.path.exists(file_path):
                return 0.0

            # 尝试使用wave模块读取WAV文件
            if file_path.lower().endswith('.wav'):
                with wave.open(file_path, 'rb') as wav_file:
                    frames = wav_file.getnframes()
                    sample_rate = wav_file.getframerate()
                    duration = frames / sample_rate
                    return duration

            # 对于其他格式，尝试使用ffprobe（如果可用）
            try:
                result = subprocess.run(
                    ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                     '-of', 'csv=p=0', file_path],
                    capture_output=True, text=True, check=True
                )
                duration = float(result.stdout.strip())
                return duration
            except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
                # 如果ffprobe不可用或失败，返回默认值
                return 0.0

        except Exception as e:
            logger.error(f'获取音频文件时长失败: {e}')
            return 0.0

    def estimate_text_duration(self, text: str) -> float:
        """估算文本播放时长（秒）"""
        try:
            if not text or not text.strip():
                return 0.0

            # 统计英文句号数量
            dot_count = text.count('.')

            # 中文平均语速：约4-5字/秒
            # 英文平均语速：约2-3词/秒
            # 这里使用一个综合估算：中文字符按4.5字/秒，英文字符按3字/秒

            chinese_chars = sum(
                1 for char in text if '\u4e00' <= char <= '\u9fff')
            english_chars = sum(
                1 for char in text if char.isascii() and char.isalpha())
            other_chars = len(text) - chinese_chars - english_chars - dot_count
            if other_chars < 0:
                other_chars = 0

            # 估算时长
            chinese_duration = chinese_chars / 4  # 中文字符
            english_duration = english_chars / 3.0   # 英文字符
            other_duration = other_chars / 4.0       # 其他字符（标点等）

            total_duration = chinese_duration + english_duration + other_duration

            # 添加一些缓冲时间（0.5秒）
            if total_duration <= 2.0:
                total_duration += 1.0

            # 每检测到一个.，额外加1秒
            total_duration += dot_count * 1.0
            if total_duration < 1.0:
                total_duration = 1.0
            total_duration *= 1.3  # 增加30%的缓冲时间，语音越长，识别越长
            print(
                f'估算文本播放时长: {total_duration},{text},{chinese_duration},{english_duration},{other_duration},{dot_count}')
            return total_duration

        except Exception as e:
            logger.error(f'估算文本播放时长失败: {e}')
            return 0.0

    def calculate_total_duration(self, task: PlayTask) -> float:
        """计算播放任务的总时长（秒）"""
        try:
            # 单次播放时长
            single_duration = 0.0

            if task.file_path:
                # 音频文件播放
                single_duration = self.get_audio_file_duration(task.file_path)
            elif task.music_text:
                # 文本播放
                single_duration = self.estimate_text_duration(task.music_text)

            # 计算总时长：单次时长 * 播放次数 + 间隔时间 * (播放次数 - 1)
            total_duration = single_duration * task.play_count
            if task.play_count > 1:
                total_duration += task.play_interval * (task.play_count - 1)

            return total_duration

        except Exception as e:
            logger.error(f'计算播放任务总时长失败: {e}')
            return 0.0
