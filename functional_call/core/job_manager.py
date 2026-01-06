"""
Job 管理（内存版）。

用于承载“长任务”（导航/动作/充电等待等）：
- 立即返回 request_id
- 后台线程执行
- 执行过程中通过 EventBus 产出事件（给语音系统流式播报）
- 托管任务生命周期，确保失败或超时后状态自动清理
"""

from __future__ import annotations

import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Any

from .event_bus import EventBus

logger = logging.getLogger(__name__)


@dataclass
class JobInfo:
    request_id: str
    session_id: str
    status: str = "running"  # running/completed/failed/cancelled
    started_ts: float = field(default_factory=lambda: time.time())
    ended_ts: float | None = None
    error: str | None = None
    result_text: str | None = None
    
    # 停止信号
    stop_event: threading.Event = field(default_factory=threading.Event)


class JobManager:
    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._lock = threading.RLock()
        self._jobs: Dict[str, JobInfo] = {}
        # 建立 session 到 request 的快速索引，用于判定 session 忙碌
        self._session_to_request: Dict[str, str] = {}

    def get(self, request_id: str) -> JobInfo | None:
        with self._lock:
            return self._jobs.get(request_id)

    def get_job(self, request_id: str) -> JobInfo | None:
        """别名方法，兼容旧代码或新逻辑"""
        return self.get(request_id)

    def get_active_job_by_session(self, session_id: str) -> JobInfo | None:
        """获取该会话当前正在运行的任务"""
        with self._lock:
            req_id = self._session_to_request.get(session_id)
            if not req_id:
                return None
            job = self._jobs.get(req_id)
            if job and job.status == "running":
                return job
            return None

    def cancel_session_job(self, session_id: str) -> None:
        """主动取消某个会话的任务"""
        job = self.get_active_job_by_session(session_id)
        if job:
            logger.info(f"🛑 正在请求取消任务: {job.request_id} (session: {session_id})")
            job.stop_event.set()
            with self._lock:
                job.status = "cancelled"

    def start(
        self,
        *,
        request_id: str,
        session_id: str,
        runner: Callable[[threading.Event], str | None],
        on_done: Callable[[JobInfo], None] | None = None,
        on_cleanup: Callable[[], None] | None = None,
    ) -> JobInfo:
        """
        启动后台托管任务。
        runner 现在接收一个 stop_event 参数，业务逻辑应周期性检查此信号以便提前退出。
        """
        job = JobInfo(request_id=request_id, session_id=session_id)
        
        with self._lock:
            # 清理该 session 的旧索引（如果存在）
            if session_id in self._session_to_request:
                old_req = self._session_to_request[session_id]
                if old_req in self._jobs and self._jobs[old_req].status == "running":
                    logger.warning(f"⚠️ Session {session_id} 已有运行中任务 {old_req}，将被新任务覆盖。")
                    self._jobs[old_req].stop_event.set()

            self._jobs[request_id] = job
            self._session_to_request[session_id] = request_id

        def _run_wrapper():
            try:
                # 执行真正的业务逻辑
                result_text = runner(job.stop_event)
                
                with self._lock:
                    job.result_text = result_text
                    # 如果不是在执行过程中被改成了 cancelled，则标记为 completed
                    if job.status == "running":
                        job.status = "completed"
                    job.ended_ts = time.time()
                
                self._event_bus.mark_done(request_id, True)
                if on_done:
                    on_done(job)
                
            except Exception as e:
                with self._lock:
                    job.status = "failed"
                    job.error = str(e)
                    job.ended_ts = time.time()
                
                self._event_bus.mark_done(request_id, True)
                logger.error(f"❌ 托管任务异常退出: {request_id}, Error: {e}")
                if on_done:
                    on_done(job)
            
            finally:
                # 无论成功失败，确保清理 session 到 request 的映射
                with self._lock:
                    if self._session_to_request.get(session_id) == request_id:
                        self._session_to_request.pop(session_id, None)
                
                if on_cleanup:
                    try:
                        on_cleanup()
                    except Exception as ce:
                        logger.error(f"清理回调执行失败: {ce}")

        t = threading.Thread(target=_run_wrapper, name=f"job-{request_id[:8]}", daemon=True)
        t.start()
        return job
