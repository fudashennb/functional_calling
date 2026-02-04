"""
配置加载（.env / 环境变量）。

注意：这里是新架构的配置入口，不与旧的 agent/config.py 耦合。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return

    # 优先：functional_call/.env；其次：cwd/.env；最后：上级目录/.env
    candidates = [
        Path(__file__).resolve().parent.parent / ".env",
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent.parent / ".env",
    ]
    for p in candidates:
        if p.exists():
            load_dotenv(p)
            break


@dataclass(frozen=True)
class Settings:
    # 服务端
    server_host: str = "0.0.0.0"
    server_port: int = 8766

    # DashScope（通义千问）
    dashscope_api_key: str | None = None
    qwen_model: str = "qwen-plus"
    qwen_timeout_s: int = 30
    # OpenAI兼容接口 base_url（DashScope）
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # 机器人（Modbus over SSH tunnel）
    modbus_host: str = "localhost"
    modbus_port: int = 1502

    # 事件流
    event_retention_max: int = 2000  # 每个request最多保留多少条事件

    # 路由增强（本地小模型：可选）
    enable_local_router_models: bool = False

    # 语音端回调推送（主动推送任务事件到语音端）
    voice_push_url: str | None = None
    voice_push_enabled: bool = True
    voice_push_timeout_s: int = 5

    # 语音中转（飞书 -> 大脑 -> 机器人语音模块）
    remote_voice_url: str = "http://127.0.0.1:8866/v1/voice/inject_stream"

    # 提示词目录
    prompts_dir: str = str(Path(__file__).resolve().parent.parent / "prompts")


def load_settings() -> Settings:
    _load_dotenv_if_available()

    def _get_int(key: str, default: int) -> int:
        v = os.getenv(key)
        if v is None or v == "":
            return default
        try:
            return int(v)
        except Exception:
            return default

    def _get_bool(key: str, default: bool) -> bool:
        v = os.getenv(key)
        if v is None or v == "":
            return default
        return v.strip().lower() in {"1", "true", "yes", "y", "on"}

    return Settings(
        server_host=os.getenv("FC_SERVER_HOST", "0.0.0.0"),
        server_port=_get_int("FC_SERVER_PORT", 8766),
        dashscope_api_key=os.getenv("DASHSCOPE_API_KEY"),
        qwen_model=os.getenv("QWEN_MODEL", "qwen-plus"),
        qwen_timeout_s=_get_int("QWEN_TIMEOUT_S", 30),
        qwen_base_url=os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        modbus_host=os.getenv("MODBUS_HOST", "localhost"),
        modbus_port=_get_int("MODBUS_PORT", 1502),
        event_retention_max=_get_int("EVENT_RETENTION_MAX", 2000),
        enable_local_router_models=_get_bool("ENABLE_LOCAL_ROUTER_MODELS", False),
        voice_push_url=os.getenv("VOICE_PUSH_URL"),
        voice_push_enabled=_get_bool("VOICE_PUSH_ENABLED", True),
        voice_push_timeout_s=_get_int("VOICE_PUSH_TIMEOUT_S", 5),
        remote_voice_url=os.getenv("REMOTE_VOICE_URL", "http://127.0.0.1:8866/v1/voice/inject_stream"),
        prompts_dir=os.getenv("PROMPTS_DIR", str(Path(__file__).resolve().parent.parent / "prompts")),
    )


