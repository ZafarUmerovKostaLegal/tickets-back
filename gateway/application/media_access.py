"""Authorization for GET /api/v1/media/{path} — deny private prefixes without domain ACL."""

from __future__ import annotations

from typing import Optional

import httpx
from fastapi import HTTPException

from infrastructure.config import get_settings

# These trees already have dedicated download APIs with real ACL.
# Serving them via the generic media route would bypass membership / ownership checks.
_PRIVATE_MEDIA_PREFIXES = (
    "chat_attachments/",
    "expenses/",
    "correspondence/",
    "vacation_leave_requests/",
    "vacation_absence_documents/",
    "hr_personnel/",
)

_TICKETS_FULL_ACCESS = {
    "IT отдел",
    "Администратор",
    "Главный администратор",
    "Партнер",
    "Офис менеджер",
    "Офис-менеджер",
}


def normalize_media_subpath(subpath: str) -> str:
    return (subpath or "").replace("\\", "/").lstrip("/")


def private_media_prefix(subpath: str) -> Optional[str]:
    rel = normalize_media_subpath(subpath)
    for prefix in _PRIVATE_MEDIA_PREFIXES:
        if rel == prefix.rstrip("/") or rel.startswith(prefix):
            return prefix.rstrip("/")
    return None


def _same_user_id(a, b) -> bool:
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return False


async def assert_media_readable(subpath: str, user: dict) -> None:
    """Raise HTTPException if the authenticated user may not read this media key."""
    rel = normalize_media_subpath(subpath)
    if not rel:
        raise HTTPException(status_code=400, detail="Invalid media path")

    blocked = private_media_prefix(rel)
    if blocked:
        raise HTTPException(
            status_code=403,
            detail=f"Use the {blocked} API to download this file",
        )

    if rel.startswith("tickets/") or rel.startswith("tickets\\"):
        filename = rel.split("/")[-1]
        await assert_ticket_attachment_readable(filename, user)
        return

    # Remaining prefixes (inventory, todo_*, desktop_*, attendance, …)
    # still require authentication via the route dependency.


async def assert_ticket_attachment_readable(filename: str, user: dict) -> None:
    safe_name = (filename or "").replace("\\", "/").split("/")[-1].strip()
    if not safe_name or safe_name in {".", ".."}:
        raise HTTPException(status_code=404, detail="Attachment not found")

    if (user or {}).get("role") in _TICKETS_FULL_ACCESS:
        return

    settings = get_settings()
    base = (settings.tickets_service_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="Tickets service not configured")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{base}/tickets/by-attachment/{safe_name}")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="Tickets service unavailable") from exc

    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail="Tickets service error")

    ticket = r.json() or {}
    if not _same_user_id(ticket.get("created_by_user_id"), user.get("id")):
        raise HTTPException(status_code=403, detail="Access denied to this attachment")
