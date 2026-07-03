from __future__ import annotations

import logging
from typing import Any

import httpx

from infrastructure.config import get_settings

_log = logging.getLogger("todos.system_notifications")


async def send_system_notification(
    *,
    recipient_user_id: int,
    title: str,
    description: str,
    notification_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    settings = get_settings()
    url = (settings.notification_push_url or "").strip()
    secret = (settings.ws_internal_secret or "").strip()
    if not url or not secret:
        return

    body: dict[str, Any] = {
        "recipient_user_id": int(recipient_user_id),
        "title": title,
        "description": description,
        "notification_type": notification_type,
    }
    if payload:
                                                                                                                                   
        body["payload"] = payload

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(url, json=body, headers={"X-Internal-Key": secret})
        if r.status_code >= 400:
            _log.warning(
                "system notification failed recipient=%s status=%s body=%s",
                recipient_user_id,
                r.status_code,
                (r.text or "")[:500],
            )
    except Exception as exc:
        _log.warning("system notification request failed recipient=%s error=%r", recipient_user_id, exc)


async def notify_todo_board_added(
    *,
    recipient_user_id: int,
    actor_user_id: int,
    board_id: int,
    board_title: str,
) -> None:
    await send_system_notification(
        recipient_user_id=recipient_user_id,
        title="Вас добавили в доску",
        description=f"Пользователь #{actor_user_id} добавил вас в доску «{board_title}».",
        notification_type="todo_board_added",
        payload={"board_id": board_id, "actor_user_id": actor_user_id},
    )


async def notify_todo_board_invited(
    *,
    recipient_user_id: int,
    actor_user_id: int,
    board_id: int,
    board_title: str,
) -> None:
    await send_system_notification(
        recipient_user_id=recipient_user_id,
        title="Вас пригласили в доску",
        description=f"Пользователь #{actor_user_id} пригласил вас в доску «{board_title}».",
        notification_type="todo_board_invited",
        payload={"board_id": board_id, "actor_user_id": actor_user_id},
    )


async def notify_todo_card_assigned(
    *,
    recipient_user_id: int,
    actor_user_id: int,
    board_id: int,
    card_id: int,
    card_title: str,
) -> None:
    await send_system_notification(
        recipient_user_id=recipient_user_id,
        title="Вас отметили в задаче",
        description=f"Пользователь #{actor_user_id} добавил вас к задаче «{card_title}».",
        notification_type="todo_card_assigned",
        payload={"board_id": board_id, "card_id": card_id, "actor_user_id": actor_user_id},
    )
