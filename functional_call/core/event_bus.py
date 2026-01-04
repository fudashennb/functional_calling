"""
事件总线（内存版）。

为每个 request_id 保存事件序列，支持 after_id 增量拉取。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .models import VoiceEvent


@dataclass
class RequestEventStream:
    request_id: str
    events: List[VoiceEvent]
    done: bool = False
    last_event_id: int = 0


class EventBus:
    def __init__(self, *, retention_max: int = 2000) -> None:
        self._retention_max = retention_max
        self._lock = threading.RLock()
        self._streams: Dict[str, RequestEventStream] = {}

    def ensure_stream(self, request_id: str) -> None:
        with self._lock:
            if request_id not in self._streams:
                self._streams[request_id] = RequestEventStream(
                    request_id=request_id, events=[], done=False, last_event_id=0
                )

    def emit(self, request_id: str, *, type: str, speak_text: str, data: dict | None = None) -> VoiceEvent:
        with self._lock:
            self.ensure_stream(request_id)
            stream = self._streams[request_id]
            stream.last_event_id += 1
            ev = VoiceEvent(
                event_id=stream.last_event_id,
                type=type,
                speak_text=speak_text,
                data=data or {},
            )
            stream.events.append(ev)
            # 事件保留上限（丢弃最老的）
            if len(stream.events) > self._retention_max:
                stream.events = stream.events[-self._retention_max :]
            return ev

    def mark_done(self, request_id: str, done: bool = True) -> None:
        with self._lock:
            self.ensure_stream(request_id)
            self._streams[request_id].done = done

    def get_events(self, request_id: str, *, after: int = 0, limit: int = 200) -> Tuple[List[VoiceEvent], bool, int]:
        with self._lock:
            if request_id not in self._streams:
                return [], False, after
            stream = self._streams[request_id]
            # after=0 -> 返回全部；after=n -> 返回 event_id > n
            new_events = [e for e in stream.events if e.event_id > after]
            if limit and len(new_events) > limit:
                new_events = new_events[:limit]
            next_after = new_events[-1].event_id if new_events else after
            return new_events, stream.done, next_after


