import logging
import json
from typing import List, Dict, Any, Optional
from abc import ABC, abstractmethod

from app.tools.base import ToolRegistry
from memory.session_store import SessionState, ConversationMessage
from llm.dashscope_provider import DashScopeLLMProvider

logger = logging.getLogger(__name__)

class ReActAgent(ABC):
    """
    Base class for ReAct Agents.
    Implements the Think-Act-Observe loop.
    """
    name: str = "base_agent"
    description: str = "Base agent"
    system_prompt: str = ""
    tools: List[str] = [] # List of tool names available to this agent

    def __init__(self, llm: DashScopeLLMProvider):
        self.llm = llm

    async def run(self, task: str, session: SessionState, context: Dict[str, Any] = None) -> str:
        """
        执行 Agent 循环。
        """
        logger.info(f"[{self.name}] 开始任务: {task}")
        
        # 记录用户任务到历史中
        session.push_message("user", f"任务 ({self.name}): {task}")
        
        step_count = 0
        max_steps = 10
        
        while step_count < max_steps:
            step_count += 1
            logger.info(f"[{self.name}] 步骤 {step_count}/{max_steps}")
            
            # 1. Think (思考并决定行动)
            response = await self._think(session)
            content = response.get("content")
            tool_calls = response.get("tool_calls")

            # 关键：必须记录 Assistant 的思考过程和工具调用意图，否则下一轮会报 400
            session.push_message("assistant", content=content, tool_calls=tool_calls)

            # 如果没有工具调用，说明任务完成或直接回答
            if not tool_calls:
                logger.info(f"[{self.name}] 最终回答: {content}")
                return content or "任务已完成。"

            # 2. Act (执行工具)
            for tool_call in tool_calls:
                tool_call_id = tool_call.get("id")
                function_name = tool_call["function"]["name"]
                arguments_str = tool_call["function"]["arguments"]
                
                try:
                    arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
                except json.JSONDecodeError:
                    observation = f"错误：工具 {function_name} 的参数 JSON 格式非法。"
                    session.push_message("tool", content=observation, tool_call_id=tool_call_id)
                    continue

                logger.info(f"[{self.name}] 执行工具: {function_name} 参数: {arguments}")
                
                # 注入上下文
                exec_context = context or {}
                result = await ToolRegistry.execute(function_name, arguments, context=exec_context)
                
                # 3. Observe (记录观察结果)
                # 关键：每一条 tool_calls 必须对应一条 tool 消息，且带上 tool_call_id
                observation = str(result)
                session.push_message("tool", content=observation, tool_call_id=tool_call_id)
                logger.info(f"[{self.name}] 观察结果: {observation}")

        return "已达到最大步骤数，未能完成任务。"

    async def _think(self, session: SessionState) -> Dict[str, Any]:
        """
        调用 LLM 获取决策，并确保消息历史完整（不破坏 assistant-tool 对）。
        """
        # 1. 构建系统提示
        messages = [{"role": "system", "content": self.system_prompt}]
        
        # 2. 安全切片逻辑：确保不切断 assistant-tool 链路
        raw_history = session.conversation
        max_history = 15
        if len(raw_history) > max_history:
            start_idx = len(raw_history) - max_history
            # 如果起始消息是 'tool'，向前多推一个，以包含对应的 'assistant'
            while start_idx > 0 and raw_history[start_idx].role == "tool":
                start_idx -= 1
            history_slice = raw_history[start_idx:]
        else:
            history_slice = raw_history

        # 3. 转换为 API 格式
        for msg in history_slice:
            m = {"role": msg.role, "content": msg.content}
            if msg.tool_calls:
                m["tool_calls"] = msg.tool_calls
            if msg.tool_call_id:
                m["tool_call_id"] = msg.tool_call_id
            messages.append(m)

        # 4. 调用接口
        tool_schemas = ToolRegistry.get_schemas_by_names(self.tools)
        response = self.llm.call_with_tools(messages, tool_schemas) 
        return response


