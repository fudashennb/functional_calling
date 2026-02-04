import logging
import asyncio
import json
import ast
import re
import threading
import time
from typing import Dict, Any, Optional, Set
from app.agents.specific_agents import ManusAgent, WorkerAgent, StatusAgent
from app.tools.planning import PLANS
from memory.session_store import SessionState
from llm.dashscope_provider import DashScopeLLMProvider
from core.voice_pusher import VoicePushNotifier

logger = logging.getLogger(__name__)

# 全局运行锁，防止同一 Session 重叠执行长耗时任务
_running_sessions: Set[str] = set()
_session_lock = threading.Lock()

class PlanningFlow:
    """
    管理规划与执行的宏观循环。
    """
    def __init__(self, llm: DashScopeLLMProvider, session: SessionState, voice_pusher: Optional[VoicePushNotifier] = None):
        self.llm = llm
        self.session = session
        self.voice_pusher = voice_pusher
        
        # 初始化 Agent
        self.manus_agent = ManusAgent(llm)
        self.worker_agent = WorkerAgent(llm)
        self.status_agent = StatusAgent(llm)
        
        # 用于分发的 Agent 注册表
        self.agents = {
            "manus": self.manus_agent,
            "worker": self.worker_agent,
            "status": self.status_agent
        }

    async def execute(self, input_text: str, stop_event: Any = None) -> str:
        """
        执行流程：规划 -> 分发 -> 循环。
        """
        # 0. 任务互斥检查
        with _session_lock:
            if self.session.session_id in _running_sessions:
                msg = f"⚠️ 会话 {self.session.session_id} 已有任务在运行中，忽略重复请求。"
                logger.warning(msg)
                return msg
            _running_sessions.add(self.session.session_id)

        logger.info(f"开始 PlanningFlow，输入任务: {input_text}")
        # 记录当前的 ID 状态
        from core.context import get_request_id, get_session_id
        logger.info(f"DEBUG: PlanningFlow.execute 入口 - ctx_req_id={get_request_id()}, ctx_sess_id={get_session_id()}, session.session_id={self.session.session_id}")
        
        try:
            # 1. 初始规划 (宏观步骤 1)
            # 计划 ID 与会话绑定或新建
            # 【修复】使用时间戳生成唯一的 Plan ID，防止 Session 复用导致读取到旧计划
            plan_id = self.session.active_plan_id or f"plan_{self.session.session_id}_{int(time.time())}"
            self.session.active_plan_id = plan_id
            
            # 工具执行上下文（如停止信号）
            context = {"stop_event": stop_event} if stop_event else {}

            # 如果计划不存在，让 Manus 创建一个
            if plan_id not in PLANS:
                logger.info("未发现活动计划，正在请求 Manus 创建计划...")
                
                # 1. 获取资源摘要 (静态)
                from core.resource_manager import resource_manager
                static_env = resource_manager.get_map_summary()
                
                # 2. 获取机器人状态 (动态 - 开局一张图)
                # 直接调用 StatusAgent 的工具（复用现有逻辑）
                # 为了速度，这里简单模拟一个 tool call 或直接调用底层
                from tools.robot_client import RobotClient
                # 注意：这里需要确保 robot 实例可用，或者通过 StatusAgent 获取
                # 由于 PlanningFlow 中没有直接持有 robot 实例，我们通过 status_agent 间接调用
                # 或者更简单：让 ManusAgent 自己去“思考”时，我们把 System Prompt 替换掉
                
                # 更好的方式：在 ManusAgent.run 中支持 system_prompt_vars
                # 我们先获取状态字符串
                try:
                    # 临时实例化一个 RobotClient 或从 orchestrator 传递进来有点麻烦
                    # 我们利用 status_agent 现有的工具
                    # 但为了解耦，最好让 ManusAgent.run 接受 dynamic_context
                    status_summary = "（正在尝试连接机器人获取状态...）"
                    # 这里为了极速，我们可以暂时留空，或者在 Orchestrator 层面做。
                    # 鉴于架构限制，我们这里尝试一次快速调用：
                    # 也可以选择让 orchestrator 传入，但改动太大。
                    # 折中方案：调用 self.status_agent 的工具逻辑
                    # 实际上，我们可以让 ManusAgent 的 run 方法内部处理
                    pass
                except:
                    pass

                # 构造 Prompt 变量
                # 为了获取动态状态，我们得调用一次 get_robot_status
                # 这是一个“系统级”调用，不算 LLM 思考
                # 我们利用 self.status_agent.tools 里的函数，但那些是字符串名字
                # 我们直接调用 app.tools.status_toolbox.get_robot_status (如果有)
                # 或者最简单的：让 ManusAgent 自己第一步还是调 list_resources? 不，我们要跳过。
                
                # 终极方案：在 PlanningFlow 初始化时，注入 robot_client，或者
                # 使用 app.tools.wrappers 中的全局函数（如果它们是全局的）
                # 检查 app.tools.wrappers
                from app.tools.wrappers import get_robot_status
                dynamic_status = get_robot_status()
                
                # 【新增】解析载货状态，辅助 Manus 决策
                load_status = "未知"
                if isinstance(dynamic_status, str):
                    if "ActionState.AT_FINISHED" in dynamic_status and "param2: 50" in dynamic_status:
                        load_status = "已载货 (顶升高度 50)"
                    elif "param2: 0" in dynamic_status:
                        load_status = "空载 (高度 0)"

                prompt_vars = {
                    "static_environment_info": static_env,
                    "dynamic_robot_status": f"{dynamic_status}\n- 载货判定：{load_status}"
                }

                manus_result = await self.manus_agent.run(
                    task=f"请为任务创建执行计划：{input_text}。计划 ID 为 '{plan_id}'。",
                    session=self.session,
                    context=context,
                    system_prompt_vars=prompt_vars  # 传递变量
                )
                
                # 校验：如果 Manus 执行完后依然没生成计划，说明输入被判定为非法或无意义
                if plan_id not in PLANS:
                    logger.warning(f"Manus 判定无效指令: {input_text}")
                    # 依然走总结逻辑，把 Manus 的一大堆解释浓缩成一句短语音
                    fail_msg = await self.manus_agent.summarize_task(input_text, [manus_result])
                    if self.voice_pusher:
                        self.voice_pusher.push_failed(
                            fail_msg, 
                            session_id=self.session.session_id,
                            request_id=self.session.active_request_id
                        )
                    return fail_msg
            
            # 2. 宏观循环 (分发步骤)
            while not stop_event or not stop_event.is_set():
                # 获取当前计划状态
                plan = PLANS.get(plan_id)
                if not plan:
                    return "错误：计划创建失败。"
                
                steps = plan.get("steps", [])
                statuses = plan.get("step_statuses", [])
                plan_title = plan.get("title", "机器人任务")
                
                # 严格防御：如果由于某种原因 steps 还是字符串，立即修正或报错
                if isinstance(steps, str):
                    logger.warning("发现 steps 为字符串，尝试解析...")
                    try:
                        steps = json.loads(steps)
                    except:
                        return "错误：计划中的步骤格式损坏。"
                
                if not isinstance(steps, list):
                    return "错误：计划步骤结构异常，无法继续。"

                # 查找下一个“待执行”的步骤
                next_step_idx = -1
                for i, status in enumerate(statuses):
                    if status == "not_started":
                        next_step_idx = i
                        break
                
                if next_step_idx == -1:
                    # 所有步骤已完成
                    logger.info("所有步骤均已处理完成。")
                    
                    # 【新逻辑】调用 LLM 生成全量复盘总结
                    all_results = plan.get("step_results", [])
                    final_summary = await self.manus_agent.summarize_task(input_text, all_results)
                    
                    if self.voice_pusher:
                        self.voice_pusher.push_completed(
                            final_summary, 
                            session_id=self.session.session_id,
                            request_id=self.session.active_request_id
                        )
                    
                    return final_summary
                
                step_desc = steps[next_step_idx]
                logger.info(f"正在处理步骤 {next_step_idx}: {step_desc}")
                
                # 【新增】任务启动播报：清洗数据，只推文字描述
                if self.voice_pusher:
                    clean_desc = self._extract_text(step_desc)
                    self.voice_pusher.push_plan(
                        clean_desc, 
                        session_id=self.session.session_id,
                        request_id=self.session.active_request_id
                    )

                # 更新状态为“进行中”
                statuses[next_step_idx] = "in_progress"
                
                # 3. 选择执行 Agent (当前使用关键词启发式路由)
                executor = self._select_agent(step_desc)
                logger.info(f"选定执行者: {executor.name}")
                
                # 4. 微观循环 (Agent 具体执行)
                try:
                    result = await executor.run(
                        task=f"请执行该步骤：{step_desc}",
                        session=self.session,
                        context=context
                    )
                    
                    # 检查逻辑失败（Agent 没报错，但返回了失败信息）
                    is_logical_failure = any(kw in result for kw in ["失败", "错误", "超时", "异常", "无法", "未能"])
                    
                    if is_logical_failure:
                        # 更新计划状态并记录结果
                        statuses[next_step_idx] = "failed"
                        PLANS[plan_id]["step_results"][next_step_idx] = result
                        
                        # 【收尾】不再推送中间的“失败”，直接生成并推送智能复盘总结
                        all_results = PLANS[plan_id]["step_results"]
                        fail_summary = await self.manus_agent.summarize_task(input_text, all_results)
                        if self.voice_pusher:
                            self.voice_pusher.push_failed(fail_summary, session_id=self.session.session_id)
                        return fail_summary
                    
                except Exception as e:
                    logger.error(f"步骤执行发生异常: {e}")
                    statuses[next_step_idx] = "error"
                    PLANS[plan_id]["step_results"][next_step_idx] = f"系统异常: {e}"
                    
                    # 【收尾】生成异常总结并停止，不再推送中间的“失败”
                    all_results = PLANS[plan_id]["step_results"]
                    err_summary = await self.manus_agent.summarize_task(input_text, all_results)
                    if self.voice_pusher:
                        self.voice_pusher.push_failed(err_summary, session_id=self.session.session_id)
                    return err_summary
                
                # 5. 更新计划状态 (成功执行)
                # 注意：此处不再推送中间的“成功”，保持语音链路简洁，只在全部结束时汇总播报
                statuses[next_step_idx] = "completed"
                PLANS[plan_id]["step_results"][next_step_idx] = result
                
                logger.info(f"步骤 {next_step_idx} 执行完毕，结果: {result}")
                
                # 可选：向 Flow 提供反馈？（目前通过更新 PLANS 隐式处理）
            
            return "任务已被停止或中断。"
        finally:
            # 0. 释放任务锁
            with _session_lock:
                if self.session.session_id in _running_sessions:
                    _running_sessions.remove(self.session.session_id)

            # 【核心修改点】任务结束（无论成功失败），立即清洗上下文
            logger.info(f"🧹 任务结束，清理 Session {self.session.session_id} 的历史噪音...")
            self.session.prune_history()
            
            # 同时清除 active_plan_id，确保下次是全新规划
            self.session.active_plan_id = None

    def _extract_text(self, raw_content: Any) -> str:
        """
        从结构化数据中提取纯中文描述，严禁输出英文和复杂标点。
        """
        if not raw_content:
            return ""
        
        val = ""
        # 1. 处理字典类型 (增加对 'step' 键的识别)
        if isinstance(raw_content, dict):
            # 优先取描述性强的键，如果 'step' 是数字则跳过
            step_val = raw_content.get("step")
            val = (raw_content.get("description") or 
                   raw_content.get("action") or 
                   (str(step_val) if isinstance(step_val, str) else None) or
                   raw_content.get("message") or 
                   raw_content.get("title") or 
                   str(raw_content))
        
        # 2. 处理字符串（尝试解析 JSON/Dict）
        elif isinstance(raw_content, str):
            clean_text = raw_content.strip()
            # 快速判断是否可能是 JSON/Dict
            if (clean_text.startswith("{") and clean_text.endswith("}")) or (clean_text.startswith("[") and clean_text.endswith("]")):
                try:
                    try:
                        data = json.loads(clean_text)
                    except:
                        data = ast.literal_eval(clean_text)
                    
                    if isinstance(data, dict):
                        step_val = data.get("step")
                        val = (data.get("description") or 
                               data.get("action") or 
                               (str(step_val) if isinstance(step_val, str) else None) or
                               data.get("message") or 
                               data.get("title") or 
                               clean_text)
                    else:
                        val = clean_text
                except:
                    val = clean_text
            else:
                val = clean_text
        else:
            val = str(raw_content)

        # 确保最终是字符串
        text = str(val)

        # 3. 终极清洗：只保留中文、数字和基本标点，剔除所有英文字母和特殊符号
        # 移除英文字母
        text = re.sub(r"[a-zA-Z]", "", text)
        # 移除所有 Markdown 符号、转义符、括号、冒号、引号等
        text = re.sub(r"[*#`\-_\\n\t\[\]{}'\"():：]", " ", text)
        # 将多个空格合并并修剪
        text = re.sub(r"\s+", " ", text).strip()
        
        return text

    def _select_agent(self, step_desc: str):
        """
        简单的关键词路由 logic (V1)。
        后续可升级为 LLM 决策路由。
        """
        desc = step_desc.lower()
        
        # 1. 优先匹配物理动作关键词 (Priority: High)
        # 如果包含动作词，不论是否包含查询词（如"查询后前往"），都倾向于给 Worker
        if any(kw in desc for kw in ["移动", "前往", "去", "导航", "充电", "顶升", "下降", "执行", "move", "nav", "charge", "lift", "put"]):
            return self.worker_agent
            
        # 2. 其次匹配状态查询关键词
        elif any(kw in desc for kw in ["查询", "状态", "电量", "检查", "有没有", "几个", "status", "check"]):
            return self.status_agent
            
        # 3. 默认交给 Manus (可能涉及重新规划或通用逻辑)
        else:
            return self.manus_agent

class FlowFactory:
    @staticmethod
    def create_flow(flow_type: str, llm: DashScopeLLMProvider, session: SessionState, voice_pusher: Optional[VoicePushNotifier] = None) -> PlanningFlow:
        if flow_type == "planning":
            return PlanningFlow(llm, session, voice_pusher)
        raise ValueError(f"未知 Flow 类型: {flow_type}")
