from __future__ import annotations

import logging
import time

from agents.base import Agent, AgentOutput
from memory.session_store import SessionState
from tools.status_toolbox import StatusToolbox
from llm.dashscope_provider import DashScopeLLMProvider, DashScopeError


logger = logging.getLogger(__name__)


class StatusAgent(Agent):
    name = "status"

    def __init__(self, *, status_toolbox: StatusToolbox, llm: DashScopeLLMProvider | None = None, system_prompt: str = "") -> None:
        super().__init__(system_prompt=system_prompt)
        self.status = status_toolbox
        self._llm = llm
        self.add_toolbox(status_toolbox)

    def handle(self, *, query: str, session: SessionState) -> AgentOutput:
        """
        状态代理的处理逻辑：
        1. 调用所有状态查询工具获取原始数据
        2. 将数据和用户问题交给 LLM 总结成一句话回答
        """
        try:
            # 获取全量状态（为了能更智能地回答综合问题）
            battery = self.status.get_battery_info()
            movement = self.status.get_movement_task_info()
            action = self.status.get_action_task_info()
            charging = self.status.is_charging()

            robot_raw_data = {
                "battery": battery,
                "movement_task": movement,
                "action_task": action,
                "is_charging": charging,
                "current_time": time.strftime("%Y-%m-%d %H:%M:%S")
            }

            if not self._llm:
                # 兜底逻辑：如果没有 LLM，使用简单的硬编码回复
                text = f"当前电量 {battery['percentage_electricity']}%，{'正在充电' if charging else '未在充电'}。"
                return AgentOutput(kind="reply", speak_text=text)

            # 让 LLM 根据原始数据和用户问题生成回答
            system = self.get_full_system_prompt()
            user_prompt = (
                f"机器人当前原始数据：{robot_raw_data}\n"
                f"用户问题：{query}\n"
                "请根据以上数据，用简洁、自然的中文回答用户的问题。如果数据中没有提到的信息，请礼貌告知。"
            )

            ans = self._llm.chat(messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt}
            ])
            return AgentOutput(kind="reply", speak_text=ans)

        except DashScopeError as de:
            logger.error(f"StatusAgent LLM 调用失败: {de}")
            return AgentOutput(kind="reply", speak_text="抱歉，我获取到了状态但暂时无法总结成语言，当前电量约为 " + str(battery.get('percentage_electricity', '未知')) + "%。")
        except Exception as e:
            logger.error(f"StatusAgent 处理异常: {e}")
            return AgentOutput(kind="reply", speak_text=f"查询状态时遇到错误：{e}")


