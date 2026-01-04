# Copyright 2025 Standard Robots Co. All rights reserved.

import logging
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler

def setup_logging():
    """统一的日志配置函数 - 支持日志轮转"""
    # 获取项目根目录
    project_root = Path(__file__).parent
    log_dir = project_root / 'logs'
    log_dir.mkdir(exist_ok=True)
    
    log_file = log_dir / 'speak_server.log'

    # 创建轮转文件处理器
    # maxBytes: 100MB = 100 * 1024 * 1024 bytes
    # backupCount: 最多保存10个备份文件
    file_handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=100 * 1024 * 1024,  # 100MB
        backupCount=10,  # 最多保存10个文件
        encoding='utf-8'
    )
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    
    # 设置日志格式
    formatter = logging.Formatter('%(asctime)s %(filename)s:%(lineno)d-%(levelname)s-%(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # 配置根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # 清除可能存在的处理器，避免重复添加
    root_logger.handlers.clear()
    
    # 添加处理器
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

# 全局初始化日志配置
setup_logging() 