from __future__ import annotations

import logging

from agents.base import Agent, AgentOutput
from memory.session_store import SessionState
from tools.status_toolbox import StatusToolbox


logger = logging.getLogger(__name__)


class StatusAgent(Agent):
    name = "status"

    def __init__(self, *, status_toolbox: StatusToolbox, system_prompt: str = "") -> None:
        super().__init__(system_prompt=system_prompt)
        self.status = status_toolbox
        self.add_toolbox(status_toolbox)

    def handle(self, *, query: str, session: SessionState) -> AgentOutput:
        q = (query or "").strip()

        try:
            if any(k in q for k in ["电池", "电量", "电池状态", "电池信息"]):
                b = self.status.get_battery_info()
                text = (
                    f"当前电量 {b['percentage_electricity']}%，电池温度 {b['temperature']}°C，"
                    f"电池电压 {b['voltage']}mV，电池电流 {b['current']}mA。"
                )
                return AgentOutput(kind="reply", speak_text=text)

            # 任务/进度/位置（这里先用任务信息）
            if any(k in q for k in ["到哪", "进度", "任务", "状态", "怎么样", "还要多久", "在干嘛", "执行"]):
                move = self.status.get_movement_task_info()
                act = self.status.get_action_task_info()
                charging = self.status.is_charging()
                text = (
                    f"当前导航状态：{move['state']}（任务号 {move['no']}，目标站点 {move['target_station']}）。"
                    f"当前动作状态：{act['state']}（任务号 {act['no']}）。"
                    f"{'正在充电。' if charging else '未在充电。'}"
                )
                return AgentOutput(kind="reply", speak_text=text)

        except Exception as e:
            return AgentOutput(kind="reply", speak_text=f"查询状态失败：{e}")

        # 默认：给一个综合摘要
        try:
            b = self.status.get_battery_info()
            charging = self.status.is_charging()
            text = f"当前电量 {b['percentage_electricity']}%。{'正在充电。' if charging else '未在充电。'}"
            return AgentOutput(kind="reply", speak_text=text)
        except Exception as e:
            return AgentOutput(kind="reply", speak_text=f"我暂时无法获取机器人状态：{e}")


