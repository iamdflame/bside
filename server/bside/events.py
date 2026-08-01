"""Event bus — one pipeline, many watchers.

Events are (1) persisted to SQLite for replay/inspection and (2) fanned out
to live SSE subscribers. The worker emits; the API streams.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections import defaultdict
from typing import Any

from bside import db
from bside.models import EventRecord

_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
_sub_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called once at app startup so worker threads can push into async queues."""
    global _loop
    _loop = loop


def emit(episode_id: str, type_: str, data: dict[str, Any] | None = None) -> EventRecord:
    """Persist an event and fan it out to live subscribers. Thread-safe."""
    rec = db.append_event(episode_id, type_, data or {})
    with _sub_lock:
        queues = list(_subscribers.get(episode_id, ())) + list(_subscribers.get("*", ()))
    if _loop is not None:
        for q in queues:
            with contextlib.suppress(RuntimeError):
                _loop.call_soon_threadsafe(q.put_nowait, rec)
    return rec


def subscribe(episode_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    with _sub_lock:
        _subscribers[episode_id].add(q)
    return q


def unsubscribe(episode_id: str, q: asyncio.Queue) -> None:
    with _sub_lock:
        _subscribers[episode_id].discard(q)
        if not _subscribers[episode_id]:
            _subscribers.pop(episode_id, None)
