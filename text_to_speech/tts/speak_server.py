# Copyright 2025 Standard Robots Co. All rights reserved.

import asyncio
import time
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
import uvicorn
import logging
import os
import sys
from pathlib import Path
import subprocess
import random
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from fastapi.responses import StreamingResponse

# ============================================================================
# 全局流式队列：用于将机器人播报实时推送到外部（如飞书）
# Key: msg_id (由外部传入)
# Value: asyncio.Queue
# ============================================================================
STREAM_QUEUES: Dict[str, asyncio.Queue] = {}

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 导入统一的日志配置
import log_config

from tts.audio_player import (
    AudioPlayer,
    SystemStateManager,
    PlayRequest,
    StatusCommand,
    PlayMode,
    PlayTask,
)
from dialog.dialog_recognize import AudioStreamReader, get_para_value

_logger = logging.getLogger(__name__)


# ============================================================================
# 回调推送接口的数据模型（接收 functional_call 推送的消息）
# ============================================================================
class VoiceCallbackRequest(BaseModel):
    """
    functional_call 推送给语音端的回调消息
    
    字段说明：
    - event_type: 事件类型（plan/fault/completed/failed）
    - speak_text: 可直接播报的中文文本
    - request_id: 任务ID（可选）
    - session_id: 会话ID（可选）
    - ext_msg_id: 外部关联ID（如飞书消息ID，可选）
    - data: 额外结构化数据（可选）
    """
    event_type: str  # plan/fault/completed/failed
    speak_text: str  # 可直接播报的中文文本
    request_id: str = ""
    session_id: str = ""
    ext_msg_id: str = ""
    data: Dict[str, Any] = {}

audio_player = None
state_manager = None
tts_app = None
play_worker_task = None  # 添加全局播放工作器任务

conversation_worker_task = None
conversation_reader = AudioStreamReader()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时的初始化
    global audio_player, state_manager, tts_app, play_worker_task

    _logger.info('语音播放服务正在启动...')
    
    # 清理播放队列中的播放任务
    _logger.info('清理播放队列中的播放任务')
    
    # 初始化全局实例（先初始化以便清理）
    audio_player = AudioPlayer()
    state_manager = SystemStateManager()
    #tts_app = audio_player.tts_app

    
    # 清理播放队列
    audio_player.clear_queue_sync()
    audio_player.stop_current()
    audio_player.is_playing = False
    
    # 清理程序相关缓存
    _logger.info('清理程序相关缓存，确保启动时状态干净')
    
    # 清理临时音频文件
    try:
        import os
        import glob
        temp_patterns = ['/tmp/tmp*.wav', '/tmp/tts_*.wav', '/tmp/output_*.wav']
        for pattern in temp_patterns:
            files = glob.glob(pattern)
            for file in files:
                try:
                    if os.path.exists(file):
                        os.unlink(file)
                        _logger.debug(f'清理临时文件: {file}')
                except:
                    pass
    except:
        pass
    
    # 清理可能的程序残留进程（只清理程序相关的）
    try:
        import subprocess
        # 只清理可能由程序创建的音频进程，不清理系统默认音频
        result = subprocess.run(['pgrep', '-f', 'aplay.*tmp'], capture_output=True, text=True)
        if result.returncode == 0:
            subprocess.run(['pkill', '-f', 'aplay.*tmp'], capture_output=True, check=False)
            _logger.info('清理程序相关的临时音频进程')
    except:
        pass
    
    _logger.info('播放队列和程序缓存清理完成')

    # 启动播放工作器
    play_worker_task = threading.Thread(target=play_queue_worker, daemon=True)
    play_worker_task.start()

    conversation_worker_task = threading.Thread(
        target=conversation_reader.conversation_stream_loop, daemon=True
    )
    conversation_worker_task.start()

    _logger.info('语音播放服务已启动')
    _logger.info('系统默认进入音乐模式')
    _logger.info('可用的API端点:')
    _logger.info('  POST /play - 播放音频/切换模式')
    _logger.info('  GET /status - 获取播放状态和系统状态')
    _logger.info('\n模式切换说明:')
    _logger.info('  - 默认音乐模式: 支持 PLAY_MUSIC, PLAY_TEXT, START_CONVERSATION')
    _logger.info('  - 对话模式: 支持 PLAY_CONVERSATION, STOP_CONVERSATION')
    _logger.info('  - 对话模式下音乐相关命令将被忽略')
    _logger.info('\n使用示例:')
    _logger.info('  - 切换模式: /play?status_command=start_conversation')
    _logger.info('  - 播放音乐: /play?file_path=/path/to/music.wav&status_command=play_music')
    _logger.info('  - 播放文本: /play?music_text=你好世界&status_command=play_text')
    _logger.info('  - 查看状态: /status')

    # 显示音频设备信息
    try:
        import sounddevice as sd
        
        # 麦克风设备信息
        try:
            from dov_device_finder import get_audio_device_id, is_target_usb_connected, TARGET_VID, TARGET_PID
            mic_connected = is_target_usb_connected()
            mic_id = get_audio_device_id()
            devices = sd.query_devices()
            if mic_id < len(devices):
                mic_device = devices[mic_id]
                _logger.info('🎤 麦克风设备信息:')
                _logger.info(f'   VID/PID: {TARGET_VID}:{TARGET_PID} ({"已连接" if mic_connected else "未连接"})')
                _logger.info(f'   设备ID: {mic_id}')
                _logger.info(f'   设备名称: {mic_device.get("name", "unknown")}')
                _logger.info(f'   输入通道数: {mic_device.get("max_input_channels", 0)}')
                _logger.info(f'   采样率: {mic_device.get("default_samplerate", 0)} Hz')
                # 获取输入音量
                try:
                    result = subprocess.run(['amixer', 'get', 'Capture'], capture_output=True, text=True, timeout=2)
                    if result.returncode == 0:
                        for line in result.stdout.split('\n'):
                            if '[' in line and '%' in line:
                                _logger.info(f'   输入音量: {line.strip()}')
                                break
                except:
                    pass
        except Exception as e:
            _logger.warning(f'获取麦克风信息失败: {e}')
        
        # 扬声器设备信息
        try:
            default_output = sd.query_devices(kind='output')
            if default_output:
                _logger.info('🔊 扬声器设备信息:')
                _logger.info(f'   设备ID: {default_output["index"]}')
                _logger.info(f'   设备名称: {default_output.get("name", "unknown")}')
                _logger.info(f'   输出通道数: {default_output.get("max_output_channels", 0)}')
                _logger.info(f'   采样率: {default_output.get("default_samplerate", 0)} Hz')
                # 获取输出音量
                try:
                    result = subprocess.run(['amixer', 'get', "'DAC VOLUME'"], capture_output=True, text=True, timeout=2)
                    if result.returncode == 0:
                        for line in result.stdout.split('\n'):
                            if '[' in line and '%' in line:
                                _logger.info(f'   输出音量: {line.strip()}')
                                break
                except:
                    pass
                # 获取USB设备VID/PID（如果是USB音频设备）
                try:
                    result = subprocess.run(['aplay', '-l'], capture_output=True, text=True, timeout=2)
                    if result.returncode == 0 and 'USB' in result.stdout:
                        usb_result = subprocess.run(['lsusb'], capture_output=True, text=True, timeout=2)
                        if usb_result.returncode == 0:
                            for line in usb_result.stdout.split('\n'):
                                if 'Audio' in line or 'audio' in line:
                                    parts = line.split()
                                    if len(parts) > 5:
                                        vid_pid = parts[5]
                                        _logger.info(f'   USB VID/PID: {vid_pid}')
                                        break
                except:
                    pass
        except Exception as e:
            _logger.warning(f'获取扬声器信息失败: {e}')
    except Exception as e:
        _logger.warning(f'获取音频设备信息失败: {e}')

    add_startup_reminder()
    _logger.info('✅ 已添加启动语音提醒')

    yield

    # 关闭时的清理
    _logger.info('语音播放服务正在关闭...')
    # 取消播放工作器任务
    if play_worker_task and play_worker_task.is_alive():
        _logger.info('等待播放工作器线程结束...')
        # 由于线程是守护线程，主程序退出时会自动结束
    # 停止音频播放
    if conversation_worker_task and conversation_worker_task.is_alive():
        conversation_reader.stop_audio_stream()

    if audio_player:
        audio_player.stop()

    # 清理TTS应用资源
   # if tts_app:
      #  try:
            # 这里可以添加TTS应用的清理逻辑
        #    _logger.info('正在清理TTS资源...')
        #    tts_app = None
        #    _logger.info('TTS资源清理完成')
      #  except Exception as e:
       #     _logger.error(f'清理TTS资源时出错: {e}')

  #  _logger.info('语音播放服务已关闭')


app = FastAPI(title='语音播放服务', description='通过API控制语音文件播放', lifespan=lifespan)

def add_startup_reminder():
    """添加启动语音提醒 - 使用播放队列系统（replace模式）"""
    try:
        audio_file = str(Path(project_root) / "music" / "语音服务已启动功能已开启.wav")
        if not Path(audio_file).exists():
            _logger.warning(f'⚠️ 启动语音文件不存在: {audio_file}')
            return
        
        # 从数据库读取音量配置（参考 dialog_recognize.py:2530-2533）
        speaker_volume = get_para_value("hmi.speaker_volume")
        if speaker_volume is not None:
            volume = float(speaker_volume) / 100.0
        else:
            volume = 0.75  # 默认音量
        
        # 创建播放任务（参考 play_conversation 的方式）
        startup_task = PlayTask(
            file_path=audio_file,
            music_text='',
            play_interval=0,
            play_count=1,
            priority=0,  # 最高优先级
            volume=volume,
            created_time=time.time(),
        )
        startup_task.status_command = StatusCommand.PLAY_MUSIC
        startup_task.duration = audio_player.get_audio_file_duration(audio_file)
        
        # 使用 replace_queue_sync 清空队列并添加任务（自动停止当前播放）
        audio_player.replace_queue_sync(startup_task)
        _logger.info(f'✅ 启动语音提醒已添加到播放队列: {audio_file}')
    except Exception as e:
        _logger.error(f'❌ 添加启动语音提醒失败: {e}')



@app.post('/start_button_conversation')
async def start_button_conversation():
    conversation_reader.start_button_conversation()
    return {'status': 'success', 'message': '已启动按钮对话'}


@app.post('/stop_button_conversation')
async def stop_button_conversation():
    conversation_reader.stop_button_conversation()
    return {'status': 'success', 'message': '已停止按钮对话'}

@app.get('/get_conversation_playing_status')
async def get_conversation_playing_status():
    return {'status': 'success', 'message': '对话播放状态', 'is_playing_now': state_manager.is_playing_now, 'last_play_end_time': state_manager.last_play_end_time}

@app.get('/get_music_playing_status')
async def get_music_playing_status():
    return {'status': 'success', 'message': '对话播放状态', 'is_music_playing_now': state_manager.is_music_playing_now, 'last_play_end_music_time': state_manager.last_play_end_music_time}

@app.get('/get_playing_status')
async def get_playing_status():
    """获取音乐播放状态，并播放启动提醒"""
    _logger.info('get_playing_status and play reminder')
    add_startup_reminder()
    return {'status': 'success', 'message': '音乐播放状态', 'is_playing': audio_player.is_playing}


@app.post('/play')
async def play_audio(request: PlayRequest):
    _logger.info(request.status_command)
    """播放音频文件或切换系统状态"""
    try:
        if state_manager.is_conversation_mode() and request.status_command in [StatusCommand.PLAY_MUSIC, StatusCommand.PLAY_TEXT]:
            _logger.info('忽略音乐请求: 当前对话模式')
            return {'status': 'ignored', 'message': 'Music paused in conversation mode'}

        if request.status_command == StatusCommand.START_CONVERSATION:
            _logger.info('start conversation')
            if state_manager.switch_to_conversation_mode():
                # 立即清理播放队列中累积的"急停已触发"等系统录音
                _logger.info('清理播放队列中累积的系统录音')
                audio_player.clear_queue_sync()
                _logger.info('强制清理累积的系统录音进程')
                if audio_player.executor:
                    audio_player.executor.shutdown(wait=True)
                _logger.info('executor shutdown')
                audio_player.executor = None
                
                audio_player.stop_current()
                # 强制终止所有 aplay 进程，清理累积的系统录音

                return {
                    'status': 'success',
                    'message': '已切换到对话模式',
                    'current_mode': 'conversation',
                }
            else:
                raise HTTPException(status_code=400, detail='当前已在对话模式或无法切换到对话模式')

        elif request.status_command == StatusCommand.STOP_CONVERSATION:
            if state_manager.switch_to_music_mode():
                return {'status': 'success', 'message': '已切换到音乐模式', 'current_mode': 'music'}
            else:
                raise HTTPException(status_code=400, detail='当前已在音乐模式或无法切换到音乐模式')

        elif request.status_command in [
            StatusCommand.PLAY_MUSIC,
            StatusCommand.PLAY_TEXT,
            StatusCommand.PLAY_CONVERSATION,
        ]:
            _logger.info(request.status_command)
            # 检查当前模式是否可以处理该命令
            if not state_manager.can_process_command(request.status_command):
                current_mode = state_manager.current_mode
                # 将模式名称转换为中文
                mode_name_map = {'music': '音乐', 'conversation': '对话'}
                current_mode_cn = mode_name_map.get(current_mode, current_mode)
                _logger.info(
                    f'当前处于{current_mode_cn}模式，{request.status_command.value}命令不生效'
                )
                if request.status_command in [StatusCommand.PLAY_MUSIC, StatusCommand.PLAY_TEXT]:
                    raise HTTPException(
                        status_code=400,
                        detail=f'当前处于{current_mode_cn}模式，{request.status_command.value}命令不生效',
                    )
                else:  # PLAY_CONVERSATION
                    raise HTTPException(
                        status_code=400, detail=f'当前处于{current_mode_cn}模式，无法播放对话内容'
                    )
            # 创建播放任务
            task = PlayTask(
                file_path=request.file_path,
                play_interval=request.play_interval,
                play_count=request.play_count,
                priority=request.priority,
                volume=request.volume,
                music_text=request.music_text,
                created_time=time.time(),
            )

            task.status_command = request.status_command  # Added: Set status_command

            # 计算并设置预估播放时长
            if task.file_path:
                _logger.info(f'get audio file duration: {task.file_path}')
                task.duration = audio_player.get_audio_file_duration(task.file_path)
            elif task.music_text:
                task.duration = audio_player.estimate_text_duration(task.music_text)

            # 修复：当 PLAY_MUSIC 使用 replace 模式时，强制改为 add 模式，避免打断提示语
            # 同步处理队列操作
            if request.mode == PlayMode.REPLACE:
                audio_player.replace_queue_sync(task)
            elif request.mode == PlayMode.ADD:
                audio_player.add_to_queue_sync(task)
            elif request.mode == PlayMode.STOP:
                audio_player.stop_current()
            if state_manager.is_conversation_mode():
                state_manager.is_playing_now = True
                state_manager.last_play_end_music_time = 0
            elif state_manager.is_music_mode():
                state_manager.is_music_playing_now = True
                state_manager.last_play_end_time = 0
            else:
                state_manager.last_play_end_time = 0
                state_manager.last_play_end_music_time = 0
            # 立即返回响应，不等待播放完成
            return {
                'status': 'success',
                'message': f'已{"替换" if request.mode == PlayMode.REPLACE else "添加"}播放任务',
                'current_mode': state_manager.current_mode,
                'task': {
                    'file_path': task.file_path,
                    'play_count': task.play_count,
                    'priority': task.priority,
                    'volume': task.volume,
                    'command': request.status_command.value,
                    'duration': task.duration,  # 添加duration字段
                },
            }

        else:
            raise HTTPException(status_code=400, detail=f'不支持的命令: {request.status_command}')

    except Exception as e:
        _logger.error(f'播放音频时发生错误: {e}')
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get('/status')
async def get_status():
    """获取播放状态和系统状态"""
    try:
        # 获取播放状态
        play_status = audio_player.get_queue_status_sync()

        # 计算队列中所有任务的预估时间
        queue_duration_info = []
        total_queue_duration = 0.0

        with audio_player.sync_lock:
            for i, task in enumerate(audio_player.play_queue):
                single_duration = task.duration  # 使用task中存储的duration
                total_duration = audio_player.calculate_total_duration(task)
                total_queue_duration += total_duration

                queue_duration_info.append(
                    {
                        'queue_position': i + 1,
                        'single_duration': single_duration,
                        'total_duration': total_duration,
                        'play_count': task.play_count,
                        'play_interval': task.play_interval,
                        'type': 'audio_file' if task.file_path else 'text',
                    }
                )

        # 获取系统状态
        system_status = {
            'current_mode': state_manager.current_mode,
            'is_music_mode': state_manager.is_music_mode(),
            'is_conversation_mode': state_manager.is_conversation_mode(),
            'available_commands': {
                'music_mode': [
                    StatusCommand.PLAY_MUSIC.value,
                    StatusCommand.PLAY_TEXT.value,
                    StatusCommand.START_CONVERSATION.value,
                ],
                'conversation_mode': [
                    StatusCommand.PLAY_CONVERSATION.value,
                    StatusCommand.STOP_CONVERSATION.value,
                ], 
            },
        }

        return {
            'status': 'success',
            'data': {
                'play_status': play_status,
                'system_status': system_status,
                'queue_duration': {
                    'total_queue_duration': total_queue_duration,
                    'queue_items': queue_duration_info,
                    'unit': 'seconds',
                },
            },
        }
    except Exception as e:
        _logger.error(f'获取状态时发生错误: {e}')
        raise HTTPException(status_code=500, detail=str(e)) from e


def play_queue_worker():
    """后台播放队列工作器"""
    while True:
        try:
            task = None
            with audio_player.sync_lock:
                if not audio_player.play_queue:
                    audio_player.is_playing = False
                    if state_manager.is_playing_now:
                        state_manager.is_playing_now = False
                        state_manager.last_play_end_time = time.time()
                    if state_manager.is_music_playing_now:
                        state_manager.is_music_playing_now = False
                        state_manager.last_play_end_music_time = time.time()
                    # 释放锁后再等待
                    pass
                else:
                    task = audio_player.play_queue.pop(0)
                    audio_player.is_playing = True
            # 如果队列为空，等待一段时间再检查
            if task is None:
                time.sleep(0.1)
                continue

            try:
                cmd = task.status_command
                if state_manager.is_conversation_mode() and cmd in [StatusCommand.PLAY_MUSIC, StatusCommand.PLAY_TEXT]:
                    _logger.warning(f'Skipping music task: Currently in conversation mode')
                    continue
            except AttributeError:
                _logger.warning('Skipping invalid task: Missing status_command attribute')
                continue

            # 播放指定次数
            # 如果play_count为0或1，都播放一次
            actual_play_count = max(1, task.play_count) if task.play_count == 0 else task.play_count
            for i in range(actual_play_count):
                try:
                    # 等待文本播放完成（通过检查进程状态）
                    if task.file_path:
                        _logger.info(f'播放文件: {task.file_path}')
                        audio_player.play_file(task.file_path, task.volume)
                    else:
                        _logger.info(f'！！！！！！！！！！！播放文本: {task.music_text}')
                        # 使用新的play_text方法播放文本
                        audio_player.play_text(task.music_text, task.volume)
                    # 如果播放数大于1，播放间隔大于0，则等待play_interval秒后再播放一次
                    if i < actual_play_count - 1 and task.play_interval > 0:
                        time.sleep(task.play_interval)
                except Exception as e:  # noqa: PERF203
                    if task.music_text:
                        _logger.error(f'播放文本失败: {e}')
                    else:
                        _logger.error(f'播放文件 {task.file_path} 失败: {e}')
                    break

        except Exception as e:
            _logger.error(f'播放队列工作器错误: {e}')
            time.sleep(1)  # 出错时等待1秒再继续


def wait_for_playback_completion():
    """等待播放完成"""
    while True:
        # 检查当前播放进程是否还在运行
        if audio_player.current_process is None:
            break

        # 检查进程是否还在运行
        if audio_player.current_process.poll() is not None:
            # 进程已结束
            break

        # 等待一段时间再检查
        time.sleep(0.1)


@app.post('/pause_music')
async def pause_music():
    _logger.info('收到暂停音乐请求')
    # 这里可以添加逻辑通知外部停止发送
    return {'status': 'success'}


@app.post('/resume_music')
async def resume_music():
    state_manager.switch_to_music_mode()  # Ensure mode and reset paused
    _logger.info('音乐请求已恢复')
    return {'status': 'success'}


@app.post('/voice/callback')
async def voice_callback(request: VoiceCallbackRequest):
    """
    接收任务事件回调，并通过对话流程（_request_server -> _synthesize_and_play_text）注入播报
    同时将播报内容同步到外部流式队列（如飞书）
    """
    try:
        # 统一使用 request_id
        target_id = request.request_id or request.ext_msg_id
        _logger.info(f"📥 收到回调推送: {request.speak_text} (event={request.event_type}, target_id={target_id})")
        
        # 1. 优先推送给外部流（如飞书）
        if target_id and target_id in STREAM_QUEUES:
            _logger.info(f"📤 正在转发到流队列 [{target_id}]: {request.speak_text}")
            await STREAM_QUEUES[target_id].put(request.speak_text)
            
            # 如果是结束类事件，发送特殊结束标记
            if request.event_type in ["completed", "failed"]:
                _logger.info(f"🏁 收到终结事件，发送 [__END__] 到队列: {target_id}")
                await STREAM_QUEUES[target_id].put("__END__")
        else:
            _logger.debug(f"ℹ️ 流队列不存在或已关闭: {target_id}")

        # 2. 定义后台执行逻辑，融入机器人本地语音播报流程
        def run_in_pipeline():
            # 融入文本清洗和日志记录流程
            ai_response = conversation_reader._request_server(None, request.speak_text)
            # 调用现有播放流程
            conversation_reader._synthesize_and_play_text(ai_response)

        # 异步执行，不阻塞回调发送方
        threading.Thread(target=run_in_pipeline, daemon=True).start()
        
        return {
            'status': 'success',
            'message': '内容已分发',
            'event_type': request.event_type
        }
    except Exception as e:
        _logger.error(f"❌ 分发回调失败: {e}")
        return {'status': 'error', 'message': str(e)}

class InjectStreamRequest(BaseModel):
    text: str
    session_id: str
    msg_id: str

@app.post('/v1/voice/inject_stream')
async def inject_stream(request: InjectStreamRequest):
    """
    外部流式指令注入。
    接收飞书文字，触发逻辑，并持续返回机器人产生的所有播报。
    """
    msg_id = request.msg_id
    # 1. 创建该请求的专属分发队列
    queue = asyncio.Queue()
    STREAM_QUEUES[msg_id] = queue
    
    _logger.info(f"📩 收到外部流式注入: {request.text} (msg_id: {msg_id})")

    async def event_generator():
        _logger.info(f"🚀 [{msg_id}] 流式生成器启动")
        try:
            # A. 伪造 AudioData 状态
            from dialog.dialog_recognize import AudioStreamReader
            mock_audio = AudioStreamReader.AudioData(
                audio_data=None, 
                vad_type="speech", 
                vad_duration=1.0, 
                vad_start_time=time.time(), 
                vad_end_time=time.time()
            )

            # B. 启动后台逻辑处理
            def process_logic():
                _logger.info(f"🧠 [{msg_id}] 启动后台处理逻辑: text='{request.text[:20]}...'")
                conversation_reader.handle_recognized_text(
                    request.text, 
                    mock_audio, 
                    ext_id=msg_id,
                    custom_session_id=request.session_id
                )
            
            threading.Thread(target=process_logic, daemon=True).start()

            # C. 持续监听队列并返回内容
            timeout_limit = 120 
            start_wait = time.time()
            
            while time.time() - start_wait < timeout_limit:
                try:
                    speak_text = await asyncio.wait_for(queue.get(), timeout=1.0)
                    
                    if speak_text == "__END__":
                        _logger.info(f"🛑 [{msg_id}] 收到结束标记，发送 [DONE] 并关闭流")
                        yield "[DONE]\n"
                        break
                        
                    _logger.info(f"📡 [{msg_id}] 发送数据到客户端: {speak_text}")
                    yield speak_text + "\n"
                except asyncio.TimeoutError:
                    # 检查是否还有活跃的线程在处理，如果没有且队列为空，可能需要异常退出
                    continue
        except Exception as e:
            _logger.error(f"💥 [{msg_id}] 流式生成器异常: {e}")
        finally:
            STREAM_QUEUES.pop(msg_id, None)
            _logger.info(f"🏁 [{msg_id}] 流式响应结束，已清理队列")

    return StreamingResponse(event_generator(), media_type="text/plain")


if __name__ == '__main__':
    uvicorn.run('tts.speak_server:app', host='0.0.0.0', port=8800, reload=False)

