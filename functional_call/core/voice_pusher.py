"""
语音端回调推送器（VoicePushNotifier）

功能：
- 在任务关键节点（计划/故障/结束）主动 POST 到语音端回调接口
- 推送三类消息：
  1. plan（计划）：任务开始前的简短说明
  2. fault（故障）：任务执行过程中出现错误
  3. completed/failed（结束）：任务成功完成或最终失败

配置：
- VOICE_PUSH_URL: 语音端回调接口地址（例如：http://10.62.232.70:8800/voice/callback）
- VOICE_PUSH_ENABLED: 是否启用推送（默认 true）
- VOICE_PUSH_TIMEOUT_S: 推送超时时间（默认 5秒）

设计原则：
- 推送失败不影响主流程（只记录日志）
- 异步推送（不阻塞任务执行）
- 自动重试1次（避免网络抖动）
"""

from __future__ import annotations

import logging
import threading
import time
import requests
from typing import Any, Dict

logger = logging.getLogger(__name__)


class VoicePushNotifier:
    """语音端回调推送器"""

    def __init__(
        self,
        push_url: str | None = None,
        enabled: bool = True,
        timeout_s: int = 5,
    ) -> None:
        self.push_url = push_url
        self.enabled = enabled and bool(push_url)
        self.timeout_s = timeout_s

        if self.enabled:
            logger.info(f"✅ 语音推送器已启用: push_url={self.push_url}, timeout={self.timeout_s}s")
        else:
            logger.info("⚠️ 语音推送器未启用（未配置 VOICE_PUSH_URL 或 VOICE_PUSH_ENABLED=false）")

    def push_plan(
        self,
        speak_text: str,
        request_id: str = "",
        session_id: str = "",
        data: Dict[str, Any] | None = None,
    ) -> None:
        """
        推送"计划"消息：任务开始前的简短说明
        
        示例：
        - "开始导航到站点1"
        - "准备执行顶升动作"
        - "开始充电流程"
        """
        self._push(
            event_type="plan",
            speak_text=speak_text,
            request_id=request_id,
            session_id=session_id,
            data=data or {},
        )

    def push_fault(
        self,
        speak_text: str,
        request_id: str = "",
        session_id: str = "",
        data: Dict[str, Any] | None = None,
    ) -> None:
        """
        推送"故障"消息：任务执行过程中出现错误
        
        示例：
        - "导航失败：路径被阻挡"
        - "顶升超时：请检查货架状态"
        - "充电异常：电池温度过高"
        """
        self._push(
            event_type="fault",
            speak_text=speak_text,
            request_id=request_id,
            session_id=session_id,
            data=data or {},
        )

    def push_completed(
        self,
        speak_text: str,
        request_id: str = "",
        session_id: str = "",
        data: Dict[str, Any] | None = None,
    ) -> None:
        """
        推送"完成"消息：任务成功完成
        
        示例：
        - "已到达站点1"
        - "顶升完成"
        - "充电完成，电池已充满"
        """
        self._push(
            event_type="completed",
            speak_text=speak_text,
            request_id=request_id,
            session_id=session_id,
            data=data or {},
        )

    def push_failed(
        self,
        speak_text: str,
        request_id: str = "",
        session_id: str = "",
        data: Dict[str, Any] | None = None,
    ) -> None:
        """
        推送"失败"消息：任务最终失败
        
        示例：
        - "导航失败，已取消任务"
        - "顶升失败，请人工检查"
        - "充电失败，请联系维护人员"
        """
        self._push(
            event_type="failed",
            speak_text=speak_text,
            request_id=request_id,
            session_id=session_id,
            data=data or {},
        )

    def _push(
        self,
        event_type: str,
        speak_text: str,
        request_id: str,
        session_id: str,
        data: Dict[str, Any],
    ) -> None:
        """
        内部推送方法（异步 + 失败不影响主流程）
        """
        # 尝试从上下文中自动获取 request_id 和 session_id (如果未提供)
        from core.context import get_request_id, get_session_id
        if not request_id:
            request_id = get_request_id() or ""
        if not session_id:
            session_id = get_session_id() or ""

        # 调试日志：无论是否启用推送，都在日志中记录内容
        logger.info(f"📢 [语音推送] 类型={event_type}, 内容=\"{speak_text}\" (启用状态={self.enabled}, req_id={request_id}, sess_id={session_id})")

        if not self.enabled:
            return

        # 异步推送（不阻塞主流程）
        t = threading.Thread(
            target=self._do_push,
            args=(event_type, speak_text, request_id, session_id, data),
            name=f"voice-push-{event_type}",
            daemon=True,
        )
        t.start()

    def _do_push(
        self,
        event_type: str,
        speak_text: str,
        request_id: str,
        session_id: str,
        data: Dict[str, Any],
    ) -> None:
        """
        实际推送逻辑（支持多目标推送 + 带指数退避重试）
        """
        payload = {
            "event_type": event_type,
            "speak_text": speak_text,
            "request_id": request_id,
            "session_id": session_id,
            "data": data,
        }

        # 解析多目标 URL
        targets = [t.strip() for t in (self.push_url or "").split(",") if t.strip()]
        
        for url in targets:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    if attempt == 0:
                        logger.info(f"📤 推送语音回调 (Target: {url}): {event_type}")
                
                    resp = requests.post(url, json=payload, timeout=self.timeout_s)
                    if resp.status_code == 200:
                        logger.info(f"✅ 推送成功: {url}")
                        break
                    logger.warning(f"⚠️ 推送失败 ({url}, HTTP {resp.status_code}): {resp.text[:50]}")
                except Exception as e:
                    logger.warning(f"⚠️ 推送异常 ({url}, {type(e).__name__}): {e}")
            
                if attempt < max_retries - 1:
                    time.sleep((attempt + 1) * 2)
                else:
                    logger.error(f"❌ 目标推送彻底失败: {url}")

