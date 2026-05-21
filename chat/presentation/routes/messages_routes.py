from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.realtime_push import push_chat_event
from infrastructure.repositories import ChatRepository
from presentation.dependencies import get_current_user_id
from presentation.schemas import MessageOut, PatchMessageBody, message_to_out

router = APIRouter(prefix="/messages", tags=["chat-messages"])


@router.patch("/{message_id}", response_model=MessageOut)
async def patch_message(
    message_id: int,
    body: PatchMessageBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    settings = get_settings()
    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message body is empty")
    if len(text) > settings.max_message_length:
        raise HTTPException(status_code=400, detail="Message too long")
    repo = ChatRepository(session)
    msg = await repo.edit_message(user_id, message_id, text)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    out = message_to_out(msg)
    recipients = await repo.member_user_ids(msg.room_id)
    await session.commit()
    await push_chat_event(
        recipient_user_ids=recipients,
        room_id=msg.room_id,
        event="message_edited",
        payload={"message": out.model_dump(by_alias=True, mode="json")},
    )
    return out


@router.delete("/{message_id}", response_model=MessageOut)
async def delete_message(
    message_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    msg = await repo.soft_delete_message(user_id, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    out = message_to_out(msg)
    recipients = await repo.member_user_ids(msg.room_id)
    await session.commit()
    await push_chat_event(
        recipient_user_ids=recipients,
        room_id=msg.room_id,
        event="message_deleted",
        payload={"message": out.model_dump(by_alias=True, mode="json")},
    )
    return out
