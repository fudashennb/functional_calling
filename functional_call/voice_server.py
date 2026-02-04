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
import requests
from pydantic import BaseModel

# 统一日志（自动配置 + 行号 + trace字段）
import log_config  # noqa: F401

from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import JSONResponse
import uvicorn

from core.config import load_settings
from core.models import VoiceEventsResponse, VoiceQueryRequest
from orchestrator.orchestrator import Orchestrator


logger = logging.getLogger(__name__)

settings = load_settings()
orchestrator = Orchestrator(settings)

app = FastAPI(title="AMR语音控制服务", description="多代理 + 事件流（进度播报）", version="1.0.0")


# 新增：转发请求的数据模型
class ForwardRequest(BaseModel):
    text: str
    session_id: str
    msg_id: str

@app.post("/v1/voice/forward")
async def voice_forward(req: ForwardRequest, background_tasks: BackgroundTasks):
    """
    [大脑中枢] 飞书 -> 大脑 -> 语音模块 (通过 SSH 隧道)
    """
    logger.info(f"🔄 [中转] 收到指令: '{req.text[:30]}' (Session: {req.session_id})")
    
    # 使用从 settings 加载的语音模块地址
    remote_voice_url = settings.remote_voice_url
    
    def _do_forward(url: str, text: str, sess_id: str, msg_id: str):
        try:
            # 使用 stream=True 开启连接后立即检查状态，不等待流式内容结束
            with requests.post(
                url,
                json={"text": text, "session_id": sess_id, "msg_id": msg_id},
                timeout=10,
                stream=True
            ) as resp:
                if resp.status_code == 200:
                    logger.info(f"✅ [中转] 指令投递成功: {msg_id}")
                else:
                    logger.warning(f"⚠️ [中转] 语音模块响应异常 ({resp.status_code}): {msg_id}")
        except Exception as e:
            logger.error(f"❌ [中转] 投递失败: {e}")

    # 使用 FastAPI 后台任务替代手动线程
    background_tasks.add_task(_do_forward, remote_voice_url, req.text, req.session_id, req.msg_id)
    
    return {"status": "forwarded", "msg_id": req.msg_id}


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


