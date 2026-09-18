from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
import httpx
from infrastructure.auth_upstream import verify_access_token_plain, verify_bearer_and_get_user
from infrastructure.config import get_settings
from presentation.schemas.ticket_schemas import (
    TicketResponse,
    TicketUpdateRequest,
    TicketSubmitApprovalRequest,
    TicketRejectApprovalRequest,
    StatusItem,
    PriorityItem,
    CommentResponse,
    CommentCreateRequest,
    CommentUpdateRequest,
)
from presentation.routes.ticket_approval import (
    TICKET_STATUS_IN_PROGRESS,
    TICKET_STATUS_ON_APPROVAL,
    TICKET_STATUS_OPEN,
    fetch_auth_user_public,
    is_partner_org_role,
    send_ticket_system_notification,
)

router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])


ROLES_FULL_ACCESS = {
    "IT отдел",
    "Администратор",
    "Главный администратор",
    "Партнер",
    "Офис менеджер",
    "Офис-менеджер",
}


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):

    user = await verify_bearer_and_get_user(request, authorization)
    return {"id": user["id"], "role": (user.get("role") or "Сотрудник").strip()}


async def _tickets_get(path: str, params: Optional[dict] = None):

    settings = get_settings()
    url = f"{settings.tickets_service_url}/tickets{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, params=params)
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=503,
            detail="Tickets service unavailable. Ensure tickets container is running (docker-compose ps).",
        ) from e
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Tickets service error")
    return r.json()


@router.get("/statuses", response_model=list[StatusItem])
async def list_statuses():
    return await _tickets_get("/statuses")


@router.get("/priorities", response_model=list[PriorityItem])
async def list_priorities():
    return await _tickets_get("/priorities")


@router.post("", response_model=TicketResponse)
async def create_ticket(
    theme: str = Form(...),
    description: str = Form(...),
    category: str = Form(...),
    priority: str = Form(...),
    attachment: Optional[UploadFile] = File(None),
    current_user: dict = Depends(get_current_user),
):
    settings = get_settings()
    form_data = {
        "theme": theme,
        "description": description,
        "category": category,
        "priority": priority,
        "created_by_user_id": str(current_user["id"]),
    }
    files = []
    if attachment and attachment.filename:
        content = await attachment.read()
        files = [
            ("attachment", (attachment.filename, content, attachment.content_type or "application/octet-stream"))
        ]
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{settings.tickets_service_url}/tickets",
            data=form_data,
            files=files if files else None,
        )
    if r.status_code == 413:
        raise HTTPException(status_code=413, detail=r.json().get("detail", "File too large"))
    if r.status_code == 400:
        raise HTTPException(status_code=400, detail=r.json().get("detail", "Bad request"))
    r.raise_for_status()
    return r.json()


@router.get("", response_model=list[TicketResponse])
async def list_tickets(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    created_by_user_id: Optional[int] = Query(None),
    include_archived: bool = Query(False),
    current_user: dict = Depends(get_current_user),
):
    params = {"skip": skip, "limit": limit, "include_archived": include_archived}
    if status is not None:
        params["status"] = status
    if priority is not None:
        params["priority"] = priority
    if category is not None:
        params["category"] = category

    if current_user["role"] not in ROLES_FULL_ACCESS:
        params["created_by_user_id"] = current_user["id"]
    return await _tickets_get("", params=params)


@router.get("/ws-url")
async def get_tickets_ws_url():

    settings = get_settings()
    base = settings.gateway_base_url or "http://localhost:1234"
    ws_base = base.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
    return {"url": f"{ws_base}/api/v1/tickets/ws/tickets"}


@router.get("/attachments/{filename}")
async def get_ticket_attachment(filename: str):
    import mimetypes

    from backend_common.media_path import safe_media_path

    settings = get_settings()
    safe_name = Path(filename).name
    path = safe_media_path(settings.media_path, f"tickets/{safe_name}")
    if path is None or not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment not found")
    media_type, _ = mimetypes.guess_type(path.name)
    return FileResponse(
        path,
        media_type=media_type or "application/octet-stream",
        filename=path.name,
    )


def _same_user_id(a, b) -> bool:

    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return False


def _can_access_ticket(ticket: dict, current_user: dict) -> bool:

    if current_user["role"] in ROLES_FULL_ACCESS:
        return True
    return _same_user_id(ticket.get("created_by_user_id"), current_user.get("id"))


_WS_ACTIONS_WITH_TICKET_UUID = frozenset(
    {"get_ticket", "update_ticket", "archive_ticket", "list_comments", "add_comment"}
)

_WS_PUBLIC_ACTIONS = frozenset({"list_statuses", "list_priorities"})


async def _ws_precheck_ticket_access(settings, ticket_uuid: str, ws_user: dict) -> str | None:

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{settings.tickets_service_url}/tickets/{ticket_uuid}")
    except httpx.RequestError:
        return "Tickets service unavailable."
    if r.status_code == 404:
        return "Ticket not found"
    if r.status_code >= 400:
        return "Tickets service error"
    ticket = r.json()
    if not _can_access_ticket(ticket, ws_user):
        return "Access denied to this ticket"
    return None


@router.get("/{ticket_uuid}", response_model=TicketResponse)
async def get_ticket(ticket_uuid: str, current_user: dict = Depends(get_current_user)):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{get_settings().tickets_service_url}/tickets/{ticket_uuid}")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Tickets service unavailable.")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Tickets service error")
    ticket = r.json()
    if not _can_access_ticket(ticket, current_user):
        raise HTTPException(status_code=403, detail="Access denied to this ticket")
    return ticket


async def _get_ticket_and_check_access(ticket_uuid: str, current_user: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{get_settings().tickets_service_url}/tickets/{ticket_uuid}")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Tickets service unavailable.")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Ticket not found")
    r.raise_for_status()
    ticket = r.json()
    if not _can_access_ticket(ticket, current_user):
        raise HTTPException(status_code=403, detail="Access denied to this ticket")
    return ticket


@router.patch("/{ticket_uuid}", response_model=TicketResponse)
async def update_ticket(
    ticket_uuid: str,
    body: TicketUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    await _get_ticket_and_check_access(ticket_uuid, current_user)
    if body.status == TICKET_STATUS_ON_APPROVAL:
        raise HTTPException(
            status_code=400,
            detail="Для статуса «На согласовании» выберите партнёра через submit-approval",
        )
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.patch(
            f"{settings.tickets_service_url}/tickets/{ticket_uuid}",
            json=body.model_dump(exclude_none=True),
        )
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if r.status_code == 400:
        raise HTTPException(status_code=400, detail=r.json().get("detail", "Bad request"))
    r.raise_for_status()
    return r.json()


@router.post("/{ticket_uuid}/submit-approval", response_model=TicketResponse)
async def submit_ticket_for_approval(
    ticket_uuid: str,
    body: TicketSubmitApprovalRequest,
    current_user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    ticket = await _get_ticket_and_check_access(ticket_uuid, current_user)
    if current_user["role"] not in ROLES_FULL_ACCESS and not _same_user_id(
        ticket.get("created_by_user_id"), current_user.get("id")
    ):
        raise HTTPException(status_code=403, detail="Нет прав отправить заявку на согласование")
    settings = get_settings()
    partner = await fetch_auth_user_public(settings, authorization, body.partner_user_id)
    if not partner:
        raise HTTPException(status_code=422, detail="Партнёр не найден")
    if not is_partner_org_role(partner.get("role"), partner.get("position")):
        raise HTTPException(status_code=422, detail="Выбранный пользователь не является партнёром")
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.patch(
            f"{settings.tickets_service_url}/tickets/{ticket_uuid}",
            json={
                "status": TICKET_STATUS_ON_APPROVAL,
                "partner_user_id": body.partner_user_id,
                "clear_rejection_comment": True,
            },
        )
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if r.status_code == 400:
        raise HTTPException(status_code=400, detail=r.json().get("detail", "Bad request"))
    r.raise_for_status()
    updated = r.json()
    theme = str(updated.get("theme") or ticket.get("theme") or "IT-заявка")
    await send_ticket_system_notification(
        settings,
        recipient_user_id=body.partner_user_id,
        title="IT-заявка на согласовании",
        description=(
            f"«{theme}» ожидает вашего решения. "
            f"Откройте заявку, чтобы согласовать или отклонить. ticket:{ticket_uuid}"
        ),
        notification_type="ticket_approval",
    )
    return updated


@router.post("/{ticket_uuid}/approve", response_model=TicketResponse)
async def approve_ticket(
    ticket_uuid: str,
    current_user: dict = Depends(get_current_user),
):
    ticket = await _get_ticket_and_check_access(ticket_uuid, current_user)
    if ticket.get("status") != TICKET_STATUS_ON_APPROVAL:
        raise HTTPException(status_code=400, detail="Заявка не ожидает согласования")
    partner_id = ticket.get("partner_user_id")
    if not _same_user_id(partner_id, current_user.get("id")):
        raise HTTPException(status_code=403, detail="Согласовать может только назначенный партнёр")
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.patch(
            f"{settings.tickets_service_url}/tickets/{ticket_uuid}",
            json={
                "status": TICKET_STATUS_IN_PROGRESS,
                "clear_rejection_comment": True,
            },
        )
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if r.status_code == 400:
        raise HTTPException(status_code=400, detail=r.json().get("detail", "Bad request"))
    r.raise_for_status()
    updated = r.json()
    theme = str(updated.get("theme") or ticket.get("theme") or "IT-заявка")
    author_id = ticket.get("created_by_user_id")
    if author_id is not None:
        try:
            await send_ticket_system_notification(
                settings,
                recipient_user_id=int(author_id),
                title="IT-заявка согласована",
                description=f"«{theme}» согласована партнёром и переведена в работу. ticket:{ticket_uuid}",
                notification_type="ticket_approved",
            )
        except (TypeError, ValueError):
            pass
    return updated


@router.post("/{ticket_uuid}/reject", response_model=TicketResponse)
async def reject_ticket(
    ticket_uuid: str,
    body: TicketRejectApprovalRequest,
    current_user: dict = Depends(get_current_user),
):
    ticket = await _get_ticket_and_check_access(ticket_uuid, current_user)
    if ticket.get("status") != TICKET_STATUS_ON_APPROVAL:
        raise HTTPException(status_code=400, detail="Заявка не ожидает согласования")
    partner_id = ticket.get("partner_user_id")
    if not _same_user_id(partner_id, current_user.get("id")):
        raise HTTPException(status_code=403, detail="Отклонить может только назначенный партнёр")
    comment = (body.comment or "").strip()
    if not comment:
        raise HTTPException(status_code=422, detail="Укажите комментарий при отказе")
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.patch(
            f"{settings.tickets_service_url}/tickets/{ticket_uuid}",
            json={
                "status": TICKET_STATUS_OPEN,
                "rejection_comment": comment,
            },
        )
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if r.status_code == 400:
        raise HTTPException(status_code=400, detail=r.json().get("detail", "Bad request"))
    r.raise_for_status()
    updated = r.json()
    theme = str(updated.get("theme") or ticket.get("theme") or "IT-заявка")
    author_id = ticket.get("created_by_user_id")
    if author_id is not None:
        try:
            await send_ticket_system_notification(
                settings,
                recipient_user_id=int(author_id),
                title="IT-заявка отклонена",
                description=(
                    f"«{theme}» отклонена партнёром. Комментарий: {comment}. ticket:{ticket_uuid}"
                ),
                notification_type="ticket_rejected",
            )
        except (TypeError, ValueError):
            pass
    return updated


@router.patch("/{ticket_uuid}/archive", response_model=TicketResponse)
async def archive_ticket(
    ticket_uuid: str,
    is_archived: bool = True,
    current_user: dict = Depends(get_current_user),
):
    await _get_ticket_and_check_access(ticket_uuid, current_user)
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.patch(
            f"{settings.tickets_service_url}/tickets/{ticket_uuid}/archive",
            params={"is_archived": is_archived},
        )
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Ticket not found")
    r.raise_for_status()
    return r.json()


@router.get("/{ticket_uuid}/comments", response_model=list[CommentResponse])
async def list_comments(ticket_uuid: str, current_user: dict = Depends(get_current_user)):
    await _get_ticket_and_check_access(ticket_uuid, current_user)
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{settings.tickets_service_url}/tickets/{ticket_uuid}/comments")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Ticket not found")
    r.raise_for_status()
    return r.json()


@router.post("/{ticket_uuid}/comments", response_model=CommentResponse)
async def create_comment(
    ticket_uuid: str,
    body: CommentCreateRequest,
    current_user: dict = Depends(get_current_user),
):
    await _get_ticket_and_check_access(ticket_uuid, current_user)
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            f"{settings.tickets_service_url}/tickets/{ticket_uuid}/comments",
            params={"user_id": current_user["id"]},
            json=body.model_dump(),
        )
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Ticket not found")
    r.raise_for_status()
    return r.json()


@router.patch("/{ticket_uuid}/comments/{comment_id}", response_model=CommentResponse)
async def update_comment(
    ticket_uuid: str,
    comment_id: int,
    body: CommentUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    await _get_ticket_and_check_access(ticket_uuid, current_user)
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.patch(
            f"{settings.tickets_service_url}/tickets/{ticket_uuid}/comments/{comment_id}",
            json=body.model_dump(),
        )
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Comment or ticket not found")
    r.raise_for_status()
    return r.json()


async def _get_ws_user(websocket: WebSocket) -> Optional[dict]:

    import urllib.parse

    query = (websocket.scope.get("query_string") or b"").decode()
    params = urllib.parse.parse_qs(query)
    tokens = params.get("token") or params.get("access_token")
    token = ""
    if tokens and tokens[0].strip():
        token = tokens[0].strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
    if not token:
        name = (get_settings().auth_session_cookie_name or "").strip()
        if name:
            token = (websocket.cookies.get(name) or "").strip()
    if not token:
        return None
    try:
        user = await verify_access_token_plain(token)
        return {"id": user["id"], "role": (user.get("role") or "Сотрудник").strip()}
    except HTTPException:
        return None


@router.websocket("/ws/tickets")
async def ws_tickets_proxy(websocket: WebSocket):
    await websocket.accept()
    ws_user = await _get_ws_user(websocket)
    settings = get_settings()
    base = settings.tickets_service_url
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    ws_url = f"{ws_base}/ws/tickets"
    ws_secret = (getattr(settings, "ws_internal_secret", None) or "").strip()
    ws_headers: dict[str, str] | None = None
    if ws_secret:
        ws_headers = {"X-Internal-Key": ws_secret}
    try:
        import asyncio
        import json
        import websockets
        async with websockets.connect(ws_url, additional_headers=ws_headers) as backend_ws:
            async def forward_from_backend():
                try:
                    async for msg in backend_ws:
                        if isinstance(msg, bytes):
                            msg = msg.decode("utf-8")
                        await websocket.send_text(msg)
                except Exception:
                    pass

            async def forward_from_client():
                while True:
                    msg = await websocket.receive_text()
                    try:
                        data = json.loads(msg)
                        action = data.get("action")
                        payload = dict(data.get("payload") or {})
                        if action not in _WS_PUBLIC_ACTIONS and not ws_user:
                            await websocket.send_json({
                                "request_id": data.get("request_id"),
                                "error": "Authorization required. Connect with ?token=...",
                            })
                            continue
                        if ws_user and action in _WS_ACTIONS_WITH_TICKET_UUID:
                            tu = (payload.get("ticket_uuid") or "").strip()
                            if not tu:
                                await websocket.send_json({
                                    "request_id": data.get("request_id"),
                                    "error": "ticket_uuid is required",
                                })
                                continue
                            err = await _ws_precheck_ticket_access(settings, tu, ws_user)
                            if err:
                                await websocket.send_json({
                                    "request_id": data.get("request_id"),
                                    "error": err,
                                })
                                continue
                        if ws_user:
                            if action == "create_ticket":
                                payload["created_by_user_id"] = ws_user["id"]
                            elif action == "add_comment":
                                payload["user_id"] = ws_user["id"]
                            elif action == "list_tickets" and (ws_user.get("role") or "").strip() not in ROLES_FULL_ACCESS:
                                payload["created_by_user_id"] = ws_user["id"]
                        data = {**data, "payload": payload}
                        msg = json.dumps(data)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    await backend_ws.send(msg)

            back_task = asyncio.create_task(forward_from_backend())
            try:
                await forward_from_client()
            except WebSocketDisconnect:
                pass
            finally:
                back_task.cancel()
                try:
                    await back_task
                except asyncio.CancelledError:
                    pass
    except Exception:
        try:
            await websocket.send_json({"error": "Connection error"})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
