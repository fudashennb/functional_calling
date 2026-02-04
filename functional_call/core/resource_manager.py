import logging
import os
import json
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class CoreResourceManager:
    """
    核心资源管理器 (单例模式)
    负责在系统启动时预加载静态资源（如地图、配置），并生成精简的自然语言摘要。
    """
    _instance = None
    _map_summary: str = "暂无地图信息。"
    _raw_maps: Dict[str, Any] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CoreResourceManager, cls).__new__(cls)
        return cls._instance

    def initialize(self, resources_dir: str = "resources") -> None:
        """
        初始化资源：扫描并解析地图文件
        """
        # 获取当前文件 (functional_call/core/resource_manager.py) 的上上级目录 (functional_call)
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # 拼接 resources 目录的绝对路径
        abs_resources_dir = os.path.join(base_path, "resources")
        
        logger.info(f"正在初始化核心资源管理器，扫描绝对路径: {abs_resources_dir}...")
        try:
            maps_dir = os.path.join(abs_resources_dir, "maps")
            if not os.path.exists(maps_dir):
                logger.warning(f"⚠️ 未找到地图目录: {maps_dir}")
                return

            # 遍历加载 JSON 地图
            loaded_maps = []
            for filename in os.listdir(maps_dir):
                if filename.endswith(".json"):
                    full_path = os.path.join(maps_dir, filename)
                    try:
                        with open(full_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            self._raw_maps[filename] = data
                            summary = self._summarize_map(filename, data)
                            loaded_maps.append(summary)
                    except Exception as e:
                        logger.error(f"❌ 加载地图 {filename} 失败: {e}")

            if loaded_maps:
                self._map_summary = "\n".join(loaded_maps)
                logger.info(f"✅ 地图资源加载并解析完成，共加载 {len(loaded_maps)} 个文件。")
            else:
                logger.warning(f"⚠️ 在 {maps_dir} 中未找到任何 .json 地图文件。")

        except Exception as e:
            logger.error(f"核心资源初始化异常: {e}")

    def _summarize_map(self, filename: str, data: Dict[str, Any]) -> str:
        """
        将复杂的地图 JSON 数据压缩为 LLM 易读的自然语言摘要。
        """
        try:
            # 提取 data 字段（兼容不同层级结构）
            map_data = data.get("data", data)
            
            # 1. 提取站点 (Stations)
            stations = map_data.get("station", [])
            station_desc_list = []
            for s in stations:
                s_id = s.get("id")
                s_name = s.get("name", "未命名站点")
                s_x = s.get("pos.x", 0)
                s_y = s.get("pos.y", 0)
                station_desc_list.append(f"- ID {s_id}: {s_name} (坐标: {s_x}, {s_y})")
            
            # 2. 提取关键点 (Nodes) - 可选，如果太多则只取前几个或忽略
            nodes = map_data.get("node", [])
            node_count = len(nodes)
            
            summary = f"【地图文件: {filename}】\n"
            summary += f"包含 {len(stations)} 个关键站点和 {node_count} 个导航节点。\n"
            if station_desc_list:
                summary += "关键站点列表：\n" + "\n".join(station_desc_list)
            else:
                summary += "无标注站点。"
            
            return summary
        except Exception as e:
            return f"【地图: {filename}】解析摘要失败: {e}"

    def get_map_summary(self) -> str:
        """获取所有已加载地图的精简摘要"""
        return self._map_summary

    def get_raw_map(self, filename: str) -> Dict[str, Any] | None:
        """获取原始地图数据（如果 Agent 确实需要深挖）"""
        return self._raw_maps.get(filename)

# 全局单例
resource_manager = CoreResourceManager()

