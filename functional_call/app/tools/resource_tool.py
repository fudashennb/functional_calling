import os
import json
import logging
from typing import List, Dict, Any, Optional
from .base import ToolRegistry

logger = logging.getLogger(__name__)

# 定义资源根目录
RESOURCES_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "resources")

@ToolRegistry.register(name="list_resources", description="列出系统中可用的静态资源文件清单（如地图、文档等）。")
def list_resources(sub_dir: Optional[str] = None) -> str:
    """
    列出资源文件。
    :param sub_dir: 可选子目录，如 'maps'
    """
    target_dir = RESOURCES_ROOT
    if sub_dir:
        target_dir = os.path.join(RESOURCES_ROOT, sub_dir)
    
    if not os.path.exists(target_dir):
        return f"错误：目录 {sub_dir or 'root'} 不存在。"
    
    try:
        files = []
        for root, _, filenames in os.walk(target_dir):
            for f in filenames:
                rel_path = os.path.relpath(os.path.join(root, f), RESOURCES_ROOT)
                files.append(rel_path)
        return json.dumps({"resources": files}, ensure_ascii=False)
    except Exception as e:
        return f"错误：列出资源失败。{str(e)}"

@ToolRegistry.register(name="read_resource", description="读取指定资源文件的内容。")
def read_resource(file_path: str) -> str:
    """
    读取资源文件内容。
    :param file_path: 相对 resources 目录的文件路径，如 'maps/fyh3.json'
    """
    full_path = os.path.join(RESOURCES_ROOT, file_path)
    
    # 安全性检查：防止目录穿越
    if not os.path.abspath(full_path).startswith(os.path.abspath(RESOURCES_ROOT)):
        return "错误：非法的文件访问路径。"
        
    if not os.path.exists(full_path):
        return f"错误：文件 {file_path} 不存在。"
    
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
            # 如果是 JSON，尝试解析并重新格式化以节省 token
            if file_path.endswith('.json'):
                data = json.loads(content)
                return json.dumps(data, ensure_ascii=False)
            return content
    except Exception as e:
        return f"错误：读取文件失败。{str(e)}"

