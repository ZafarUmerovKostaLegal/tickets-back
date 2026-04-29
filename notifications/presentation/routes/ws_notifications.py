import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from backend_common.ws_internal_auth import reject_unless_valid_internal_ws_key

from infrastructure.config import get_settings
from infrastructure.database import async_session_factory
from infrastructure.repositories import NotificationRepository
from application.ports import NotificationFilters
from application.use_cases import (
    CreateNotificationUseCase,
    GetNotificationUseCase,
    ListNotificationsUseCase,
    UpdateNotificationUseCase,
    ArchiveNotificationUseCase,
    DeleteNotificationUseCase,
)

router = APIRouter(tags=["ws"])


def _notification_to_dict(n):
    return {
        "id": n.id,
        "uuid": n.uuid,
        "title": n.title,
        "description": n.description,
        "photo_path": n.photo_path,
        "is_archived": n.is_archived,
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "updated_at": n.updated_at.isoformat() if n.updated_at else None,
    }


@router.websocket("/ws/notifications")
async def ws_notifications(websocket: WebSocket):
    settings = get_settings()
    if not await reject_unless_valid_internal_ws_key(websocket, settings.ws_internal_secret):
        return
    await websocket.accept()
    while True:
        try:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
        except WebSocketDisconnect:
            break
        except json.JSONDecodeError:
            await websocket.send_json({"error": "Invalid JSON"})
            continue
        action = msg.get("action")
        payload = msg.get("payload") or {}
        request_id = msg.get("request_id")

        def reply(result=None, error=None):
            out = {"request_id": request_id}
            if error is not None:
                out["error"] = error
            else:
                out["result"] = result
            return out

        async with async_session_factory() as session:
            try:
                repo = NotificationRepository(session)

                if action == "list_notifications":
                    filters = NotificationFilters(
                        skip=payload.get("skip", 0),
                        limit=payload.get("limit", 50),
                        include_archived=payload.get("include_archived", False),
                    )
                    uc = ListNotificationsUseCase(repo)
                    items = await uc.execute(filters)
                    await websocket.send_json(reply(result=[_notification_to_dict(n) for n in items]))
                    continue

                if action == "get_notification":
                    uc = GetNotificationUseCase(repo)
                    n = await uc.execute(payload.get("notification_uuid", ""))
                    if not n:
                        await websocket.send_json(reply(error="Notification not found"))
                    else:
                        await websocket.send_json(reply(result=_notification_to_dict(n)))
                    continue

                if action == "create_notification":
                    uc = CreateNotificationUseCase(repo)
                    n = await uc.execute(
                        title=payload.get("title", ""),
                        description=payload.get("description", ""),
                        photo_path=payload.get("photo_path"),
                    )
                    await session.commit()
                    await websocket.send_json(reply(result=_notification_to_dict(n)))
                    continue

                if action == "update_notification":
                    uc = UpdateNotificationUseCase(repo)
                    n = await uc.execute(
                        notification_uuid=payload.get("notification_uuid", ""),
                        title=payload.get("title"),
                        description=payload.get("description"),
                        photo_path=payload.get("photo_path"),
                    )
                    if not n:
                        await websocket.send_json(reply(error="Notification not found"))
                    else:
                        await session.commit()
                        await websocket.send_json(reply(result=_notification_to_dict(n)))
                    continue

                if action == "archive_notification":
                    uc = ArchiveNotificationUseCase(repo)
                    n = await uc.execute(
                        notification_uuid=payload.get("notification_uuid", ""),
                        is_archived=payload.get("is_archived", True),
                    )
                    if not n:
                        await websocket.send_json(reply(error="Notification not found"))
                    else:
                        await session.commit()
                        await websocket.send_json(reply(result=_notification_to_dict(n)))
                    continue

                if action == "delete_notification":
                    uc = DeleteNotificationUseCase(repo)
                    ok = await uc.execute(payload.get("notification_uuid", ""))
                    if not ok:
                        await websocket.send_json(reply(error="Notification not found"))
                    else:
                        await session.commit()
                        await websocket.send_json(reply(result={"deleted": True}))
                    continue

                await websocket.send_json(reply(error=f"Unknown action: {action}"))

            except Exception:
                await session.rollback()
                await websocket.send_json(reply(error="Internal error"))
