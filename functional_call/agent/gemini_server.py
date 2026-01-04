#!/usr/bin/env python3
# Copyright 2025 Standard Robots Co. All rights reserved.
"""
Gemini HTTP服务器
提供HTTP接口进行对话输入和结果获取
"""

import os
import sys
import logging
from pathlib import Path

# 添加项目根目录到Python路径（必须在导入其他模块之前）
# 使用绝对路径解析，确保正确找到项目根目录
if __file__:
    project_root = Path(__file__).resolve().parent.parent
else:
    # 如果 __file__ 不存在（某些特殊环境），从当前目录向上查找
    project_root = Path.cwd()
    while project_root.name != 'text_to_speech' and project_root.parent != project_root:
        project_root = project_root.parent

sys.path.insert(0, str(project_root))

# 导入统一的日志配置（必须在导入其他项目模块之前）
import log_config

# 现在可以导入其他依赖
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

# 导入Gemini Agent
# 直接运行脚本时，需要将agent目录添加到路径
# 必须在导入 gemini_agent 之前设置路径，确保 gemini_agent.py 中的路径设置能正确执行
agent_dir = Path(__file__).parent
if str(agent_dir) not in sys.path:
    sys.path.insert(0, str(agent_dir))

# 确保项目根目录在路径中（gemini_agent.py 需要这个路径来导入 log_config）
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

try:
    from .gemini_agent import GeminiAgent
except (ImportError, ValueError):
    # 如果作为独立模块运行，使用绝对导入
    # 此时路径已经设置好，gemini_agent.py 中的路径设置代码会正确执行
    from gemini_agent import GeminiAgent

_logger = logging.getLogger(__name__)

# 创建FastAPI应用
app = FastAPI(title="Gemini对话服务", description="通过HTTP接口提供Gemini AI对话功能")

# 全局Gemini Agent实例
gemini_agent = None


class ChatRequest(BaseModel):
    """对话请求模型"""
    message: str
    callback: str = None  # 可选的回调URL


class ChatResponse(BaseModel):
    """对话响应模型"""
    success: bool
    message: str = None
    error: str = None


def init_gemini_agent():
    """初始化Gemini Agent"""
    global gemini_agent
    try:
        _logger.info("🔄 正在初始化Gemini Agent...")
        
        # 临时清除可能导致问题的代理环境变量（特别是 socks 代理）
        # 保存原始值以便后续恢复
        original_proxies = {}
        proxy_vars = ['ALL_PROXY', 'all_proxy', 'HTTP_PROXY', 'http_proxy', 
                     'HTTPS_PROXY', 'https_proxy']
        for var in proxy_vars:
            if var in os.environ:
                original_proxies[var] = os.environ[var]
                # 如果是 socks 代理，临时清除
                if 'socks' in os.environ[var].lower():
                    del os.environ[var]
                    _logger.debug(f"临时清除 {var} (socks 代理)")
        
        try:
            gemini_agent = GeminiAgent()
            _logger.info("✅ Gemini Agent初始化成功")
            return True
        finally:
            # 恢复原始代理设置
            for var, value in original_proxies.items():
                os.environ[var] = value
        
    except Exception as e:
        _logger.error(f"❌ Gemini Agent初始化失败: {e}", exc_info=True)
        return False


@app.on_event("startup")
async def startup_event():
    """应用启动时初始化"""
    init_gemini_agent()


@app.get("/")
async def root():
    """根路径"""
    return {
        "service": "Gemini对话服务",
        "status": "running",
        "agent_available": gemini_agent is not None
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    if gemini_agent is None:
        raise HTTPException(status_code=503, detail="Gemini Agent未初始化")
    return {
        "status": "healthy",
        "agent_available": True
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    对话接口
    
    Args:
        request: 对话请求，包含message字段
        
    Returns:
        ChatResponse: 对话响应
    """
    if gemini_agent is None:
        # 尝试重新初始化
        if not init_gemini_agent():
            return ChatResponse(
                success=False,
                error="Gemini Agent未初始化，请检查配置和依赖"
            )
    
    try:
        _logger.info(f"📤 收到对话请求: {request.message[:100]}...")
        
        # 调用Gemini Agent
        response_text = gemini_agent.send_message(request.message)
        
        _logger.info(f"📥 返回对话响应: {response_text[:100]}...")
        
        return ChatResponse(
            success=True,
            message=response_text
        )
        
    except Exception as e:
        _logger.error(f"❌ 处理对话请求失败: {e}", exc_info=True)
        return ChatResponse(
            success=False,
            error=f"处理请求时出错: {str(e)}"
        )


@app.post("/reload")
async def reload_agent():
    """重新加载Gemini Agent"""
    global gemini_agent
    try:
        gemini_agent = None
        if init_gemini_agent():
            return {"success": True, "message": "Gemini Agent重新加载成功"}
        else:
            return {"success": False, "message": "Gemini Agent重新加载失败"}
    except Exception as e:
        _logger.error(f"❌ 重新加载失败: {e}")
        return {"success": False, "message": f"重新加载失败: {str(e)}"}


if __name__ == "__main__":
    # 从环境变量读取配置
    host = os.getenv("GEMINI_SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("GEMINI_SERVER_PORT", "8766"))
    
    _logger.info(f"🚀 启动Gemini HTTP服务器: {host}:{port}")
    
    uvicorn.run(
        app,  # 直接传递 app 对象
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )

