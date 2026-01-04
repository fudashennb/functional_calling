"""
请求上下文（trace/session/request）管理。

使用 contextvars 在日志中自动注入 trace_id / session_id / request_id，
便于串联一次语音请求的全链路。
"""

from __future__ import annotations

from contextlib import contextmanager
import contextvars
from dataclasses import dataclass
from typing import Iterator, Optional


_trace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_id", default=None)
_session_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("session_id", default=None)
_request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)


def get_trace_id() -> str | None:
    return _trace_id_var.get()


def get_session_id() -> str | None:
    return _session_id_var.get()


def get_request_id() -> str | None:
    return _request_id_var.get()


@dataclass(frozen=True)
class ContextSnapshot:
    trace_id: str | None
    session_id: str | None
    request_id: str | None


def snapshot() -> ContextSnapshot:
    return ContextSnapshot(
        trace_id=get_trace_id(),
        session_id=get_session_id(),
        request_id=get_request_id(),
    )


@contextmanager
def request_context(
    *,
    trace_id: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
) -> Iterator[None]:
    """
    在当前上下文设置 trace/session/request，退出时自动恢复。
    """
    t1 = _trace_id_var.set(trace_id)
    t2 = _session_id_var.set(session_id)
    t3 = _request_id_var.set(request_id)
    try:
        yield
    finally:
        _trace_id_var.reset(t1)
        _session_id_var.reset(t2)
        _request_id_var.reset(t3)


