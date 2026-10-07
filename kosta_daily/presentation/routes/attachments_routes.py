from __future__ import annotations

import urllib.parse
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import get_session
from infrastructure.file_storage import resolve_storage_path
from infrastructure.repositories import ChatRepository
from presentation.dependencies import get_current_user_id

router = APIRouter(prefix="/attachments", tags=["chat-attachments"])

_INLINE_MIME_PREFIXES = ("image/", "video/", "audio/", "application/pdf")


def _content_disposition(file_name: str, content_type: str) -> str:
    inline = any(content_type.startswith(p) for p in _INLINE_MIME_PREFIXES)
    kind = "inline" if inline else "attachment"
    quoted = urllib.parse.quote(file_name or "file")
    return f"{kind}; filename*=UTF-8''{quoted}"


@router.get("/{attachment_id}/file")
async def download_attachment(
    attachment_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    found = await repo.get_attachment_with_room(attachment_id)
    if not found:
        raise HTTPException(status_code=404, detail="Attachment not found")
    att, room_id = found
    if await repo.is_member(user_id, room_id) is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    path = resolve_storage_path(att.storage_key)
    if path is None:
        raise HTTPException(status_code=404, detail="File missing")
    return FileResponse(
        path,
        media_type=att.content_type or "application/octet-stream",
        headers={"Content-Disposition": _content_disposition(att.file_name, att.content_type or "")},
    )
