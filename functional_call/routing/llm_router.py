from __future__ import annotations

import logging
import json
import re
from dataclasses import dataclass
from typing import Literal

from llm.dashscope_provider import DashScopeLLMProvider

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class LLMRouteResult:
    agent: str  # planner/command/status
    reason: str | None = None

class LLMRouter:
    def __init__(self, llm: DashScopeLLMProvider) -> None:
        self._llm = llm
        self._system_prompt = (
            "你是工业 AMR 机器人意图分发专家。请根据用户的输入，将其路由到以下三个 Agent 之一：\n\n"
            "1. command: 物理动作指令。\n"
            "   - 特征：用户想要改变机器人的物理状态（如：启停充电、开始移动、执行特定的硬件动作）。\n"
            "   - 示例：“开始充电”、“停止”、“去一号站”、“顶升”。\n\n"
            "2. status: 状态查询请求。\n"
            "   - 特征：用户只想获取信息，不涉及物理状态改变（如：查询电量、位置、是否在线、连接情况、站点数量、进度汇报）。\n"
            "   - 关键：只要包含疑问、核实语义（尤其是带“吗”、“是否”、“怎么样”、“几个”），且不要求执行新动作，一律归为此类。\n"
            "   - 示例：“在充电吗”、“电量多少”、“当前有几个站点”、“任务进度如何”。\n\n"
            "3. planner: 复杂任务规划。\n"
            "   - 特征：包含多步顺序、逻辑条件或任务编排。隐含多步动作的简短指令也属于此类。\n"
            "   - 示例：“先去 A 拿货再去 B”、“如果没电了就去充电”、“去充电”（隐含移动+充电）。\n\n"
            "输出规范：\n"
            "- 你必须只返回 JSON，不要输出任何额外文字。\n"
            "- 格式如下：{\"agent\": \"command\" | \"status\" | \"planner\", \"reason\": \"简短判定理由\"}"
        )

    def route(self, query: str) -> LLMRouteResult:
        """
        调用 LLM 进行意图分发
        """
        try:
            logger.info(f"🔮 LLM 正在进行路由判定: \"{query}\"")
            
            raw_response = self._llm.chat(messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": f"请分发指令：\"{query}\""}
            ])

            # 尝试解析 JSON
            try:
                # 处理可能包含的 markdown 块
                json_str = raw_response
                if "```json" in raw_response:
                    m = re.search(r"```json\s*([\s\S]*?)\s*```", raw_response)
                    if m:
                        json_str = m.group(1)
                elif "{" in raw_response:
                    m = re.search(r"\{[\s\S]*\}", raw_response)
                    if m:
                        json_str = m.group(0)

                obj = json.loads(json_str)
                agent = obj.get("agent", "status")
                reason = obj.get("reason", "")
                
                # 校验合法性
                if agent not in ["command", "status", "planner"]:
                    agent = "status"
                
                logger.info(f"✅ LLM 路由结果：{agent} (理由: {reason})")
                return LLMRouteResult(agent=agent, reason=reason)

            except Exception as parse_err:
                logger.error(f"❌ LLM 响应解析 JSON 失败: {parse_err}, 原始响应: {raw_response}")
                return LLMRouteResult(agent="status", reason="parse_failed_fallback")

        except Exception as e:
            logger.error(f"❌ LLM 路由调用失败: {e}")
            return LLMRouteResult(agent="status", reason="api_error_fallback")

