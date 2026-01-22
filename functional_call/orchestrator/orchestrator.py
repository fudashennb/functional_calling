from __future__ import annotations

import logging
import uuid
import threading
import asyncio
import re
from pathlib import Path

from core.config import Settings
from core.context import request_context
from core.event_bus import EventBus
from core.job_manager import JobManager
from core.language import LanguageService
from core.models import VoiceQueryRequest, VoiceQueryResponse
from core.voice_pusher import VoicePushNotifier
from llm.dashscope_provider import DashScopeLLMProvider
from app.flows.planning_flow import FlowFactory
from app.tools.wrappers import initialize_tools
import app.tools # 确保所有工具都被注册
from memory.session_store import SessionStore
from tools.robot_client import RobotClient
from tools.nav_toolbox import NavToolbox
from tools.action_toolbox import ActionToolbox
from tools.status_toolbox import StatusToolbox

logger = logging.getLogger(__name__)

class Orchestrator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lang_service = LanguageService(default_lang="zh")

        self.sessions = SessionStore()
        self.event_bus = EventBus(retention_max=settings.event_retention_max)
        self.job_manager = JobManager(self.event_bus)

        # 语音推送器（主动推送任务事件到语音端）
        self.voice_pusher = VoicePushNotifier(
            push_url=settings.voice_push_url,
            enabled=settings.voice_push_enabled,
            timeout_s=settings.voice_push_timeout_s,
        )

        self.robot = RobotClient(settings.modbus_host, settings.modbus_port)

        # 初始化模块化工具箱
        self.nav_toolbox = NavToolbox(self.robot)
        self.action_toolbox = ActionToolbox(self.robot)
        self.status_toolbox = StatusToolbox(self.robot)
        
        # Initialize global tool wrappers
        initialize_tools(self.nav_toolbox, self.action_toolbox, self.status_toolbox)

        self.llm = None
        if settings.dashscope_api_key:
            self.llm = DashScopeLLMProvider(
                api_key=settings.dashscope_api_key,
                model=settings.qwen_model,
                timeout_s=settings.qwen_timeout_s,
                base_url=settings.qwen_base_url,
            )
        else:
            logger.warning("未检测到 DASHSCOPE_API_KEY：LLM能力将不可用。")

        if self.llm is None:
            raise RuntimeError("DashScope API Key 缺失，系统无法初始化。")

    def warm_up(self) -> None:
        """
        预热所有耗时资源。
        """
        logger.info("系统预热完成。")

    def handle_query(self, req: VoiceQueryRequest) -> tuple[int, VoiceQueryResponse]:
        trace_id = str(uuid.uuid4())
        session_id = req.session_id or str(uuid.uuid4())
        # 优先使用传入的 request_id，如果为空则生成新的
        request_id = req.request_id if req.request_id else str(uuid.uuid4())

        logger.info(f"DEBUG: orchestrator.handle_query 收到请求 - req_id={req.request_id}, sess_id={req.session_id} -> 选定 request_id={request_id}, session_id={session_id}")

        # 清洗 query：移除常见的 ASR 模型标识符（如 <|en|>, <|zh|> 等）
        query = re.sub(r"<\|.*?\|>", "", req.query).strip()

        with request_context(trace_id=trace_id, session_id=session_id, request_id=request_id):
            logger.info(f"🎤 收到语音请求: \"{query}\" (原始: \"{req.query}\", session_id: {session_id}, request_id: {request_id})")
            
            # 确认上下文变量已生效
            from core.context import get_request_id, get_session_id
            logger.info(f"DEBUG: 上下文变量检查 - ctx_req_id={get_request_id()}, ctx_sess_id={get_session_id()}")
            
            # 语言检测
            lang = req.lang or self.lang_service.detect(query).lang
            session = self.sessions.get_or_create(session_id, lang=lang)
            session.lang = lang
            session.active_request_id = request_id
            
            # 确保 session 引用 job_manager (用于自愈)
            session._job_manager = self.job_manager

            # 记录对话
            session.push_message("user", query)

            # 创建 Planning Flow
            flow = FlowFactory.create_flow("planning", self.llm, session, self.voice_pusher)
            
            # 202 立即响应，告知用户正在处理
            # 注意：对于查询类任务，最好能同步返回。但 PlanningFlow 架构统一为异步/流式更自然。
            # 这里我们统一走 JobManager 托管。
            
            self.event_bus.ensure_stream(request_id)
            
            def _flow_runner(stop_event: threading.Event) -> str | None:
                # 在新线程中必须重新建立上下文，否则 contextvars 会丢失
                with request_context(trace_id=trace_id, session_id=session_id, request_id=request_id):
                    logger.info(f"DEBUG: 线程内上下文已重建 - req_id={request_id}")
                    # 在同步线程中运行异步 Flow
                    # JobManager 在线程中运行此函数
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        result = loop.run_until_complete(flow.execute(query, stop_event))
                        return result
                    finally:
                        loop.close()
            
            def _cleanup():
                if session.active_request_id == request_id:
                    session.active_request_id = None
                logger.info(f"🧹 Flow 任务清理完成: {request_id}")

            self.job_manager.start(
                request_id=request_id,
                session_id=session_id,
                runner=_flow_runner,
                on_cleanup=_cleanup
            )
            
            # 初始反馈语
            first_response = "收到，正在思考中"
            
            resp = VoiceQueryResponse(
                resultCode=202,
                resultMsg=first_response,
                session_id=session_id,
                request_id=request_id,
                status="accepted",
                lang=lang,
            )
            return 202, resp
