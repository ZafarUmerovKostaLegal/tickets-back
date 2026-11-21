from __future__ import annotations

import hmac

from fastapi import WebSocket


def has_valid_internal_ws_key(secret: str | None, header_key: str | None) -> bool:
    required = (secret or "").strip()
    provided = (header_key or "").strip()
    if not required or not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), required.encode("utf-8"))


async def reject_unless_valid_internal_ws_key(websocket: WebSocket, secret: str | None) -> bool:
    if has_valid_internal_ws_key(secret, websocket.headers.get("x-internal-key")):
        return True
    await websocket.close(code=1008)
    return False
