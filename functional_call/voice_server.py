#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
语音交互服务端（FastAPI）

对接你的语音系统：
- POST /v1/voice/query  { "query": "..." , "session_id": "..." }
  - 短任务：200 + resultMsg（可直接播报）
  - 长任务：202 + request_id + 第一条 resultMsg（开始执行…），随后用事件流播报

- GET /v1/voice/events/{request_id}?after=0&limit=200
  - 返回增量事件（每条事件包含 speak_text）
"""

import logging
import os

# 统一日志（自动配置 + 行号 + trace字段）
import log_config  # noqa: F401

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

from core.config import load_settings
from core.models import VoiceEventsResponse, VoiceQueryRequest
from orchestrator.orchestrator import Orchestrator


logger = logging.getLogger(__name__)

settings = load_settings()
orchestrator = Orchestrator(settings)

app = FastAPI(title="AMR语音控制服务", description="多代理 + 事件流（进度播报）", version="1.0.0")


@app.on_event("startup")
async def startup_event():
    """
    服务启动时：预热模型（加载到内存），确保第一个请求不超时。
    """
    orchestrator.warm_up()


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/v1/voice/query")
async def voice_query(req: VoiceQueryRequest):
    status_code, resp = orchestrator.handle_query(req)
    return JSONResponse(status_code=status_code, content=resp.model_dump())


@app.get("/v1/voice/events/{request_id}")
async def voice_events(request_id: str, after: int = 0, limit: int = 200):
    events, done, next_after = orchestrator.event_bus.get_events(request_id, after=after, limit=limit)
    resp = VoiceEventsResponse(
        request_id=request_id,
        done=done,
        next_after=next_after,
        events=events,
    )
    return JSONResponse(status_code=200, content=resp.model_dump())


if __name__ == "__main__":
    host = os.getenv("FC_SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("FC_SERVER_PORT", "8766"))
    logger.info(f"🚀 启动语音服务: {host}:{port}")
    uvicorn.run(app, host=host, port=port, reload=False, log_level="info")


