from __future__ import annotations

import logging
import uuid
from pathlib import Path

from core.config import Settings
from core.context import request_context
from core.event_bus import EventBus
from core.job_manager import JobManager
from core.language import LanguageService
from core.models import VoiceQueryRequest, VoiceQueryResponse
from core.voice_pusher import VoicePushNotifier
from llm.dashscope_provider import DashScopeLLMProvider
from memory.session_store import SessionStore
from routing.router import IntentRouter
from tools.robot_client import RobotClient
from tools.nav_toolbox import NavToolbox
from tools.action_toolbox import ActionToolbox
from tools.status_toolbox import StatusToolbox
from tools.diag_toolbox import DiagToolbox

from agents.chat_agent import ChatAgent
from agents.command_agent import CommandAgent
from agents.diagnostics_agent import DiagnosticsAgent
from agents.planner_agent import PlannerAgent
from agents.status_agent import StatusAgent


logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lang_service = LanguageService(default_lang="zh")

        self.sessions = SessionStore()
        self.event_bus = EventBus(retention_max=settings.event_retention_max)
        self.job_manager = JobManager(self.event_bus)

        # 提示词加载
        self.prompts = self._load_prompts(settings.prompts_dir)

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
        self.diag_toolbox = DiagToolbox(self.robot, settings.modbus_host, settings.modbus_port)

        self.llm = None
        if settings.dashscope_api_key:
            self.llm = DashScopeLLMProvider(
                api_key=settings.dashscope_api_key,
                model=settings.qwen_model,
                timeout_s=settings.qwen_timeout_s,
                base_url=settings.qwen_base_url,
            )
        else:
            logger.warning("未检测到 DASHSCOPE_API_KEY：LLM能力将不可用（仍可执行确定性指令）。")

        self.router = IntentRouter(enable_local_models=settings.enable_local_router_models)

        # agents（注入特定的工具箱）
        self.command_agent = CommandAgent(
            event_bus=self.event_bus,
            job_manager=self.job_manager,
            llm=self.llm,
            voice_pusher=self.voice_pusher,
            system_prompt=self.prompts.get("command", ""),
            nav_toolbox=self.nav_toolbox,
            action_toolbox=self.action_toolbox,
        )
        self.status_agent = StatusAgent(
            status_toolbox=self.status_toolbox, 
            system_prompt=self.prompts.get("status", "")
        )
        self.diagnostics_agent = DiagnosticsAgent(
            diag_toolbox=self.diag_toolbox,
            system_prompt=self.prompts.get("diagnostics", "")
        )
        self.chat_agent = ChatAgent(
            llm=self.llm, 
            system_prompt=self.prompts.get("chat", "")
        )
        self.planner_agent = PlannerAgent(
            event_bus=self.event_bus,
            job_manager=self.job_manager,
            command_agent=self.command_agent,
            voice_pusher=self.voice_pusher,
            system_prompt=self.prompts.get("planner", ""),
            llm=self.llm,
            nav_toolbox=self.nav_toolbox,
            action_toolbox=self.action_toolbox,
        )

    def _load_prompts(self, prompts_dir: str) -> dict[str, str]:
        """从目录加载所有 .txt 提示词文件"""
        prompts = {}
        path = Path(prompts_dir)
        if not path.exists():
            logger.warning(f"提示词目录不存在: {prompts_dir}")
            return prompts
        
        for f in path.glob("*.txt"):
            try:
                prompts[f.stem] = f.read_text(encoding="utf-8")
                logger.info(f"✅ 已加载提示词文件: {f.name}")
            except Exception as e:
                logger.error(f"❌ 加载提示词文件失败 {f.name}: {e}")
        return prompts

    def warm_up(self) -> None:
        """
        预热所有耗时资源（本地模型、连接等）。
        """
        logger.info("正在执行系统预热：预加载本地路由模型...")
        self.router.warm_up()
        logger.info("系统预热完成。")

    def handle_query(self, req: VoiceQueryRequest) -> tuple[int, VoiceQueryResponse]:
        trace_id = str(uuid.uuid4())
        session_id = req.session_id or str(uuid.uuid4())
        request_id = str(uuid.uuid4())

        with request_context(trace_id=trace_id, session_id=session_id, request_id=request_id):
            logger.info(f"🎤 收到语音请求: \"{req.query}\" (session_id: {session_id})")
            # 语言：显式优先，否则检测
            lang = req.lang or self.lang_service.detect(req.query).lang
            session = self.sessions.get_or_create(session_id, lang=lang)
            session.lang = lang

            # 记录对话
            session.push_message("user", req.query)

            agent_name = self.router.route(query=req.query, session=session)
            logger.info(f"路由选择Agent：{agent_name}")

            if agent_name == "planner":
                out = self.planner_agent.handle(query=req.query, session=session)
            elif agent_name == "command":
                out = self.command_agent.handle(query=req.query, session=session)
            elif agent_name == "status":
                out = self.status_agent.handle(query=req.query, session=session)
            elif agent_name == "diagnostics":
                out = self.diagnostics_agent.handle(query=req.query, session=session)
            else:
                out = self.chat_agent.handle(query=req.query, session=session)

            # assistant 记忆（仅对同步回答存）
            if out.kind == "reply":
                session.push_message("assistant", out.speak_text)
                resp = VoiceQueryResponse(
                    resultCode=0,
                    resultMsg=out.speak_text,
                    session_id=session_id,
                    request_id=None,
                    status="completed",
                    lang=lang,
                )
                return 200, resp

            # 异步任务
            resp = VoiceQueryResponse(
                resultCode=202,
                resultMsg=out.speak_text,
                session_id=session_id,
                request_id=out.request_id,
                status="accepted",
                lang=lang,
            )
            return 202, resp


