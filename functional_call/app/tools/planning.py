from typing import List, Dict, Any, Optional
import json
from .base import ToolRegistry

# In-memory storage for plans (Plan Board)
# Structure: { plan_id: { "steps": [...], "status": "...", "title": "..." } }
PLANS = {}

@ToolRegistry.register(name="planning", description="任务计划管理工具。用于创建、更新或查询执行计划。")
def planning(command: str, plan_id: str, steps: List[str] = None, step_index: int = None, step_status: str = None, title: str = None):
    """
    管理执行计划。
    参数:
        command: 指令，可选 "create" (创建), "get" (获取), "mark_step" (标记步骤), "update_steps" (更新步骤)
        plan_id: 计划的唯一 ID
        steps: 步骤描述列表（用于创建/更新）
        step_index: 要修改的步骤索引（用于标记步骤）
        step_status: 步骤状态，可选 "not_started", "in_progress", "completed", "failed"
        title: 计划标题
    """
    global PLANS
    
    if command == "create":
        if not steps:
            return "错误：创建计划时必须提供 'steps'。"
        
        # 修复序列化陷阱：确保 steps 是列表
        if isinstance(steps, str):
            try:
                steps = json.loads(steps)
            except:
                return "错误：'steps' 格式非法，必须是 JSON 数组字符串或列表。"
        
        if not isinstance(steps, list):
            return "错误：'steps' 必须是一个列表。"

        PLANS[plan_id] = {
            "title": title or "未命名计划",
            "steps": [str(s) for s in steps],
            "step_statuses": ["not_started"] * len(steps),
            "step_results": [""] * len(steps),
            "status": "active"
        }
        return f"计划已创建，ID: {plan_id}。共 {len(steps)} 个步骤。"

    elif command == "get":
        plan = PLANS.get(plan_id)
        if not plan:
            return f"错误：未找到 ID 为 {plan_id} 的计划。"
        return json.dumps(plan, ensure_ascii=False)

    elif command == "mark_step":
        plan = PLANS.get(plan_id)
        if not plan:
            return f"错误：未找到 ID 为 {plan_id} 的计划。"
        
        if step_index is None or step_index < 0 or step_index >= len(plan["steps"]):
            return f"错误：无效的步骤索引 {step_index}。"
            
        plan["step_statuses"][step_index] = step_status
        return f"步骤 {step_index} 已标记为 {step_status}。"

    elif command == "update_steps":
        plan = PLANS.get(plan_id)
        if not plan:
            return f"错误：未找到 ID 为 {plan_id} 的计划。"
        if not steps:
            return "错误：更新时必须提供 'steps'。"
        
        # 简单覆盖现有步骤
        plan["steps"] = steps
        plan["step_statuses"] = ["not_started"] * len(steps)
        return f"计划步骤已更新。新步骤数：{len(steps)}。"

    return f"错误：未知指令 {command}。"
