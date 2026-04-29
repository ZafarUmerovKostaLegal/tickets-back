

import asyncio
from typing import Any

from fastapi import WebSocket

QUEUE_MAX = 256


class TicketsWSHub:


    def __init__(self):
        self._subs: list[tuple[WebSocket, asyncio.Queue]] = []
        self._lock = asyncio.Lock()

    async def subscribe(self, ws: WebSocket) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        async with self._lock:
            self._subs.append((ws, q))
        return q

    async def unsubscribe(self, ws: WebSocket) -> None:
        async with self._lock:
            self._subs = [(w, q) for w, q in self._subs if w is not ws]

    async def broadcast_event(self, payload: dict[str, Any]) -> None:
        out = {**payload, "push": True}
        async with self._lock:
            subs = list(self._subs)
        for _ws, q in subs:
            try:
                q.put_nowait(out)
            except asyncio.QueueFull:
                try:
                    _ = q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(out)
                except asyncio.QueueFull:
                    pass


hub = TicketsWSHub()


async def notify_ticket_event(
    event: str,
    *,
    ticket_uuid: str,
    comment_id: int | None = None,
) -> None:
    data: dict[str, Any] = {"event": event, "ticket_uuid": ticket_uuid}
    if comment_id is not None:
        data["comment_id"] = comment_id
    await hub.broadcast_event(data)
