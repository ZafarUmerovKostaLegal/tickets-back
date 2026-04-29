from __future__ import annotations

from fastapi import WebSocket


def has_valid_internal_ws_key(secret: str | None, header_key: str | None) -> bool:
    required = (secret or "").strip()
    provided = (header_key or "").strip()
    return bool(required) and provided == required


async def reject_unless_valid_internal_ws_key(websocket: WebSocket, secret: str | None) -> bool:
    if has_valid_internal_ws_key(secret, websocket.headers.get("x-internal-key")):
        return True
    await websocket.close(code=1008)
    return False
