#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一的日志配置模块（新架构增强版）

目标：
- 日志包含：文件名 + 行号
- 全链路可追踪：trace_id / session_id / request_id（通过 contextvars 自动注入）
- 兼容现有代码：import log_config 即可生效
"""

import logging
import os
from pathlib import Path
from datetime import datetime

# trace上下文（可选：若新模块不存在也不影响旧功能）
try:
    from core.context import get_trace_id, get_session_id, get_request_id  # type: ignore
except Exception:
    get_trace_id = lambda: None  # noqa: E731
    get_session_id = lambda: None  # noqa: E731
    get_request_id = lambda: None  # noqa: E731

# 获取项目根目录
_project_root = Path(__file__).resolve().parent if '__file__' in globals() else Path.cwd()
_log_dir = _project_root / 'logs'

# 创建日志目录
_log_dir.mkdir(exist_ok=True)


def setup_logging():
    """
    配置统一的日志系统
    设置根日志记录器的配置，所有子记录器都会继承这些设置
    """
    # 获取根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    # 清除已有的处理器（避免重复添加）
    root_logger.handlers.clear()
    
    class _ContextFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            # 给formatter提供字段
            record.trace_id = get_trace_id() or "-"
            record.session_id = get_session_id() or "-"
            record.request_id = get_request_id() or "-"
            return True

    # 创建日志格式（包含文件名和行号 + trace上下文）
    # defaults 参数确保在没有 trace 上下文时也能正常工作
    log_format = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(name)s - [%(filename)s:%(lineno)d] '
        '- trace=%(trace_id)s session=%(session_id)s req=%(request_id)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        defaults={"trace_id": "-", "session_id": "-", "request_id": "-"},
    )
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(log_format)
    
    # 创建文件处理器（所有日志写入同一个文件）
    log_file = _log_dir / f'all_main_{datetime.now().strftime("%Y%m%d")}.log'
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(log_format)
    
    # 添加处理器到根记录器
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    # 添加上下文过滤器
    root_logger.addFilter(_ContextFilter())
    
    return root_logger


# 自动执行日志配置
setup_logging()

