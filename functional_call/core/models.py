"""
对外HTTP接口与内部事件/结果的通用数据模型。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class VoiceQueryRequest(BaseModel):
    # 你的语音系统目前只会发 query，我们兼容扩展字段
    query: str = Field(..., description="用户的文本指令（来自语音转文本）")
    session_id: str | None = Field(default=None, description="会话ID（建议由客户端维持）")
    callback_url: str | None = Field(default=None, description="可选：事件回调URL")
    lang: str | None = Field(default=None, description="可选：zh/en，缺省自动检测")


class VoiceQueryResponse(BaseModel):
    # 保持你语音系统兼容：必须包含 resultCode / resultMsg
    resultCode: int = 0
    resultMsg: str = ""

    # 扩展字段（语音系统可选用）
    session_id: str | None = None
    request_id: str | None = None
    status: str | None = None  # accepted/completed/failed
    lang: str | None = None


class VoiceEvent(BaseModel):
    event_id: int
    type: str  # started/progress/step_done/completed/failed/info
    speak_text: str
    ts: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    data: Dict[str, Any] = Field(default_factory=dict)


class VoiceEventsResponse(BaseModel):
    resultCode: int = 0
    resultMsg: str = "OK"
    request_id: str
    done: bool = False
    next_after: int = 0
    events: List[VoiceEvent] = Field(default_factory=list)


