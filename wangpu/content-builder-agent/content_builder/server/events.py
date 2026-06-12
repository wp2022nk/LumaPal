"""Lightweight in-process events for LAN app realtime updates."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any


def json_sse_line(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


class AppEventBroker:
    def __init__(self) -> None:
        self.listeners: dict[str, set[asyncio.Queue[tuple[str, dict[str, Any]]]]] = defaultdict(set)

    async def publish(self, event: str, payload: dict[str, Any], *, thread_id: str | None = None) -> None:
        normalized = {**payload, "event": event, "time": int(time.time())}
        if thread_id:
            normalized.setdefault("thread_id", thread_id)
        keys = {"*"}
        if thread_id:
            keys.add(thread_id)
        for key in keys:
            for queue in list(self.listeners.get(key, set())):
                await queue.put((event, normalized))

    @contextlib.asynccontextmanager
    async def subscribe(self, thread_id: str = "*") -> AsyncIterator[asyncio.Queue[tuple[str, dict[str, Any]]]]:
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self.listeners[thread_id or "*"].add(queue)
        try:
            yield queue
        finally:
            self.listeners[thread_id or "*"].discard(queue)


app_events = AppEventBroker()
