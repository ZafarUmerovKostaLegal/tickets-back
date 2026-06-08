from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import get_session
from infrastructure.models import ChatMessageModel
from infrastructure.realtime_push import push_chat_event
from infrastructure.repositories import ChatRepository
from presentation.dependencies import get_current_user_id
from presentation.schemas import (
    CreatePollBody,
    MessageOut,
    VotePollBody,
    message_to_out,
    reply_to_out,
)

router = APIRouter(tags=["chat-polls"])


async def _message_out_with_poll(repo: ChatRepository, msg: ChatMessageModel, user_id: int) -> MessageOut:
    reply_out = None
    if msg.reply_to_message_id is not None:
        parent = await repo.get_message_in_room(msg.reply_to_message_id, msg.room_id)
        if parent:
            reply_out = reply_to_out(parent)
    poll = await repo.get_poll_by_message_id(msg.id)
    votes: list = []
    if poll:
        vmap = await repo.votes_for_poll_ids([poll.id])
        votes = vmap.get(poll.id, [])
    rxmap = await repo.reactions_for_message_ids([msg.id])
    atts = await repo.attachments_for_message_ids([msg.id])
    return message_to_out(
        msg,
        atts.get(msg.id),
        reply_to=reply_out,
        reactions=rxmap.get(msg.id),
        poll=poll,
        poll_votes=votes,
        viewer_id=user_id,
    )


@router.post("/rooms/{room_id}/polls", response_model=MessageOut, status_code=201)
async def create_poll(
    room_id: int,
    body: CreatePollBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    if await repo.is_member(user_id, room_id) is None:
        raise HTTPException(status_code=404, detail="Room not found")
    if not await repo.can_user_post(user_id, room_id):
        raise HTTPException(status_code=403, detail="Only admins can post in this channel")
    reply_id = body.reply_to_message_id
    if reply_id is not None:
        parent = await repo.get_message_in_room(reply_id, room_id)
        if not parent:
            raise HTTPException(status_code=400, detail="Reply target message not found in this room")
    result = await repo.create_poll_message(
        user_id,
        room_id,
        kind=body.kind,
        question=body.question,
        options=body.options,
        allows_multiple=body.allows_multiple,
        is_anonymous=body.is_anonymous,
        correct_option_index=body.correct_option_index,
        explanation=body.explanation,
        reply_to_message_id=reply_id,
    )
    if not result:
        raise HTTPException(status_code=400, detail="Cannot create poll")
    msg, _poll = result
    out = await _message_out_with_poll(repo, msg, user_id)
    recipients = await repo.member_user_ids(room_id)
    await session.commit()
    await push_chat_event(
        recipient_user_ids=recipients,
        room_id=room_id,
        event="message",
        payload={"message": out.model_dump(by_alias=True, mode="json")},
    )
    return out


@router.post("/polls/{poll_id}/vote", response_model=MessageOut)
async def vote_poll(
    poll_id: int,
    body: VotePollBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    result = await repo.vote_poll(user_id, poll_id, body.option_index)
    if not result:
        raise HTTPException(status_code=400, detail="Cannot vote on this poll")
    poll, votes = result
    msg_r = await session.execute(select(ChatMessageModel).where(ChatMessageModel.id == poll.message_id))
    msg = msg_r.scalars().one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    out = message_to_out(msg, poll=poll, poll_votes=votes, viewer_id=user_id)
    recipients = await repo.member_user_ids(msg.room_id)
    await session.commit()
    await push_chat_event(
        recipient_user_ids=recipients,
        room_id=msg.room_id,
        event="poll_vote",
        payload={"message": out.model_dump(by_alias=True, mode="json")},
    )
    return out


@router.post("/polls/{poll_id}/close", response_model=MessageOut)
async def close_poll(
    poll_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    poll = await repo.close_poll(user_id, poll_id)
    if not poll:
        raise HTTPException(status_code=403, detail="Cannot close this poll")
    msg_r = await session.execute(select(ChatMessageModel).where(ChatMessageModel.id == poll.message_id))
    msg = msg_r.scalars().one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    out = await _message_out_with_poll(repo, msg, user_id)
    recipients = await repo.member_user_ids(msg.room_id)
    await session.commit()
    await push_chat_event(
        recipient_user_ids=recipients,
        room_id=msg.room_id,
        event="poll_closed",
        payload={"message": out.model_dump(by_alias=True, mode="json")},
    )
    return out
