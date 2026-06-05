from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select as _sql_select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.models import ChatMessageModel
from infrastructure.realtime_push import push_chat_event
from infrastructure.repositories import ChatRepository
from presentation.dependencies import get_current_user_id
from presentation.schemas import (
    MessageOut,
    PatchMessageBody,
    ReactionCountOut,
    ToggleReactionBody,
    message_to_out,
    reactions_to_out,
)

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
    atts_by_msg = await repo.attachments_for_message_ids([msg.id])
    out = message_to_out(msg, atts_by_msg.get(msg.id))
    recipients = await repo.member_user_ids(msg.room_id)
    await session.commit()
    await push_chat_event(
        recipient_user_ids=recipients,
        room_id=msg.room_id,
        event="message_edited",
        payload={"message": out.model_dump(by_alias=True, mode="json")},
    )
    return out


@router.post("/{message_id}/reactions", response_model=list[ReactionCountOut])
async def toggle_reaction(
    message_id: int,
    body: ToggleReactionBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    updated = await repo.toggle_reaction(user_id, message_id, body.emoji)
    if updated is None:
        raise HTTPException(status_code=404, detail="Message not found or not a member")
    out = reactions_to_out(updated)
    r = await session.execute(_sql_select(ChatMessageModel.room_id).where(ChatMessageModel.id == message_id))
    room_id = r.scalar_one_or_none()
    await session.commit()
    if room_id is not None:
        recipients = await repo.member_user_ids(int(room_id))
        await push_chat_event(
            recipient_user_ids=recipients,
            room_id=int(room_id),
            event="reaction",
            payload={
                "messageId": message_id,
                "reactions": [rx.model_dump(by_alias=True, mode="json") for rx in out],
            },
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
