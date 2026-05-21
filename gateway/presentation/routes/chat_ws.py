from __future__ import annotations

import json
from collections import defaultdict

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from backend_common.ws_internal_auth import has_valid_internal_ws_key
from infrastructure.auth_upstream import verify_access_token_plain
from infrastructure.config import get_settings

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


class ChatSocketHub:
    def __init__(self) -> None:
        self._by_user: dict[int, set[WebSocket]] = defaultdict(set)

    def register(self, user_id: int, websocket: WebSocket) -> None:
        self._by_user[int(user_id)].add(websocket)

    def unregister(self, websocket: WebSocket) -> None:
        empty: list[int] = []
        for user_id, sockets in self._by_user.items():
            sockets.discard(websocket)
            if not sockets:
                empty.append(user_id)
        for user_id in empty:
            self._by_user.pop(user_id, None)

    async def send_to_users(self, user_ids: list[int], payload: dict) -> None:
        for uid in {int(x) for x in user_ids}:
            for ws in list(self._by_user.get(uid, set())):
                try:
                    await ws.send_json(payload)
                except Exception:
                    self.unregister(ws)


chat_socket_hub = ChatSocketHub()


class ChatInternalPushBody(BaseModel):
    recipient_user_ids: list[int] = Field(..., min_length=1)
    room_id: int
    event: str
    payload: dict = Field(default_factory=dict)


async def get_user_from_token(token: str | None, websocket: WebSocket) -> dict | None:
    raw = (token or "").replace("Bearer ", "").strip()
    if not raw:
        name = (get_settings().auth_session_cookie_name or "").strip()
        if name:
            raw = (websocket.cookies.get(name) or "").strip()
    if not raw:
        return None
    try:
        user = await verify_access_token_plain(raw)
        return {"id": user["id"], "role": user.get("role") or "Сотрудник"}
    except HTTPException:
        return None


@router.post("/internal/push", status_code=204)
async def internal_chat_push(
    body: ChatInternalPushBody,
    x_internal_key: str | None = Header(None, alias="X-Internal-Key"),
):
    settings = get_settings()
    if not has_valid_internal_ws_key(settings.ws_internal_secret, x_internal_key):
        raise HTTPException(status_code=403, detail="Invalid internal key")
    await chat_socket_hub.send_to_users(
        body.recipient_user_ids,
        {
            "type": body.event,
            "room_id": body.room_id,
            "payload": body.payload,
        },
    )


@router.websocket("/ws")
async def ws_chat(websocket: WebSocket):
    await websocket.accept()
    registered_user_id: int | None = None
    connected_user: dict | None = None
    initial_token = websocket.query_params.get("token")
    initial_user = await get_user_from_token(initial_token, websocket)
    if initial_user and initial_user.get("id") is not None:
        connected_user = initial_user
        registered_user_id = int(initial_user["id"])
        chat_socket_hub.register(registered_user_id, websocket)
        await websocket.send_json({"type": "connected", "user_id": registered_user_id})
    while True:
        try:
            raw = await websocket.receive_text()
        except WebSocketDisconnect:
            chat_socket_hub.unregister(websocket)
            break
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await websocket.send_json({"type": "error", "error": "Invalid JSON"})
            continue
        token = msg.get("token") or (msg.get("payload") or {}).get("token")
        user = await get_user_from_token(token, websocket) if token else connected_user
        if user is None:
            user = await get_user_from_token(None, websocket)
        if not user:
            await websocket.send_json(
                {
                    "type": "error",
                    "error": "Authorization required. Send token in query ?token= or in message.",
                }
            )
            continue
        connected_user = user
        if registered_user_id != int(user["id"]):
            registered_user_id = int(user["id"])
            chat_socket_hub.register(registered_user_id, websocket)
        if msg.get("type") == "ping":
            await websocket.send_json({"type": "pong"})
