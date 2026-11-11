from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend_common.media_response import media_file_response
from infrastructure.database import get_session
from infrastructure.file_storage import resolve_storage_path
from infrastructure.repositories import ChatRepository
from presentation.dependencies import get_current_user_id

router = APIRouter(prefix="/attachments", tags=["chat-attachments"])


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
    return media_file_response(
        path,
        download_name=att.file_name,
        declared_media_type=att.content_type,
    )
