"""
Job 管理（内存版）。

用于承载“长任务”（导航/动作/充电等待等）：
- 立即返回 request_id
- 后台线程执行
- 执行过程中通过 EventBus 产出事件（给语音系统流式播报）
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

from .event_bus import EventBus


@dataclass
class JobInfo:
    request_id: str
    session_id: str
    status: str = "running"  # running/completed/failed
    started_ts: float = field(default_factory=lambda: time.time())
    ended_ts: float | None = None
    error: str | None = None
    result_text: str | None = None


class JobManager:
    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._lock = threading.RLock()
        self._jobs: Dict[str, JobInfo] = {}

    def get(self, request_id: str) -> JobInfo | None:
        with self._lock:
            return self._jobs.get(request_id)

    def start(
        self,
        *,
        request_id: str,
        session_id: str,
        runner: Callable[[], str | None],
        on_done: Callable[[JobInfo], None] | None = None,
    ) -> JobInfo:
        """
        启动后台任务。runner 返回最终给用户的总结文本（可选）。
        """
        job = JobInfo(request_id=request_id, session_id=session_id)
        with self._lock:
            self._jobs[request_id] = job

        def _run():
            try:
                result_text = runner()
                job.result_text = result_text
                job.status = "completed"
                job.ended_ts = time.time()
                self._event_bus.mark_done(request_id, True)
                if on_done:
                    on_done(job)
            except Exception as e:
                job.status = "failed"
                job.error = str(e)
                job.ended_ts = time.time()
                self._event_bus.mark_done(request_id, True)
                if on_done:
                    on_done(job)

        t = threading.Thread(target=_run, name=f"job-{request_id[:8]}", daemon=True)
        t.start()
        return job


