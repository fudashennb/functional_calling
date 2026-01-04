"""
agents：多代理系统。

每个 Agent 负责一个清晰的能力边界：
- command：会改变机器人状态的指令（长任务，走事件流）
- status：状态查询（短任务，同步返回）
- diagnostics：排障/恢复建议（短任务）
- planner：复杂任务拆解（长任务，编排其它agent/工具）
- chat：闲聊/澄清（短任务）
"""


