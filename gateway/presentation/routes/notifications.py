import asyncio
import json
from collections import defaultdict

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
import websockets
import httpx
from backend_common.rbac_ui_permissions import NOTIFICATIONS_WRITE, role_in_set
from backend_common.ws_internal_auth import has_valid_internal_ws_key
from infrastructure.auth_upstream import verify_access_token_plain
from infrastructure.config import get_settings

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

WRITE_ACTIONS = {"create_notification", "update_notification", "delete_notification", "archive_notification"}


class NotificationSocketHub:
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

    async def send_to_user(self, user_id: int, payload: dict) -> None:
        sockets = list(self._by_user.get(int(user_id), set()))
        for ws in sockets:
            try:
                await ws.send_json(payload)
            except Exception:
                self.unregister(ws)


socket_hub = NotificationSocketHub()


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


async def forward_to_notifications_service(message: dict) -> dict:

    settings = get_settings()
    base = settings.notifications_service_url
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    ws_url = f"{ws_base}/ws/notifications"
    ws_secret = (getattr(settings, "ws_internal_secret", None) or "").strip()
    ws_headers: dict[str, str] | None = None
    if ws_secret:
        ws_headers = {"X-Internal-Key": ws_secret}
    try:
        async with websockets.connect(ws_url, additional_headers=ws_headers) as ws:
            await ws.send(json.dumps(message))
            raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
            return json.loads(raw)
    except Exception:
        return {"error": "Service unavailable", "request_id": message.get("request_id")}


def _notification_service_http_url(path: str) -> str:
    base = get_settings().notifications_service_url.rstrip("/")
    return f"{base}/notifications{path}"


@router.post("/system", status_code=201)
async def create_system_notification(
    request: Request,
    x_internal_key: str | None = Header(None, alias="X-Internal-Key"),
):
    settings = get_settings()
    if not has_valid_internal_ws_key(settings.ws_internal_secret, x_internal_key):
        raise HTTPException(status_code=403, detail="Invalid internal key")
    body = await request.json()
    recipient_user_id = body.get("recipient_user_id")
    if recipient_user_id is None:
        raise HTTPException(status_code=400, detail="recipient_user_id is required")

    headers = {"X-Internal-Key": x_internal_key or ""}
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            _notification_service_http_url("/system"),
            json=body,
            headers=headers,
        )
    if r.status_code not in (200, 201):
        raise HTTPException(status_code=r.status_code, detail=r.text or "Notifications service error")

    notification = r.json()
    await socket_hub.send_to_user(
        int(recipient_user_id),
        {"type": "notification", "notification": notification},
    )
    return notification


@router.websocket("/ws")
async def ws_notifications(websocket: WebSocket):
    await websocket.accept()
    registered_user_id: int | None = None
    connected_user: dict | None = None
    initial_token = websocket.query_params.get("token")
    initial_user = await get_user_from_token(initial_token, websocket)
    if initial_user and initial_user.get("id") is not None:
        connected_user = initial_user
        registered_user_id = int(initial_user["id"])
        socket_hub.register(registered_user_id, websocket)
        await websocket.send_json({"type": "connected", "user_id": registered_user_id})
    while True:
        try:
            raw = await websocket.receive_text()
        except WebSocketDisconnect:
            socket_hub.unregister(websocket)
            break
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await websocket.send_json({"error": "Invalid JSON"})
            continue

        token = msg.get("token") or (msg.get("payload") or {}).get("token")
        user = await get_user_from_token(token, websocket) if token else connected_user
        if user is None:
            user = await get_user_from_token(None, websocket)
        if not user:
            await websocket.send_json({
                "request_id": msg.get("request_id"),
                "error": "Authorization required. Send 'token' in message or in payload.",
            })
            continue
        connected_user = user
        if registered_user_id != int(user["id"]):
            registered_user_id = int(user["id"])
            socket_hub.register(registered_user_id, websocket)

        action = msg.get("action")
        if action in WRITE_ACTIONS and not role_in_set(user["role"], NOTIFICATIONS_WRITE):
            await websocket.send_json({
                "request_id": msg.get("request_id"),
                "error": "Only Partner, IT department and Office manager can create, edit, archive or delete notifications.",
            })
            continue

        payload = dict(msg.get("payload") or {})
        payload.pop("token", None)
        forward_msg = {"action": action, "payload": payload, "request_id": msg.get("request_id")}

        response = await forward_to_notifications_service(forward_msg)
        await websocket.send_json(response)
