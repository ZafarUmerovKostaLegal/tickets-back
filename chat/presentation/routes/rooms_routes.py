from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.file_storage import save_chat_file
from infrastructure.realtime_push import push_chat_event
from infrastructure.repositories import ChatRepository
from presentation.dependencies import get_current_user_id
from presentation.schemas import (
    AddRoomMembersBody,
    CreateChannelRoomBody,
    CreateDmRoomBody,
    CreateGroupRoomBody,
    MarkReadBody,
    MessageOut,
    MessagesListOut,
    PostMessageBody,
    RoomMembersListOut,
    RoomMemberOut,
    RoomOut,
    RoomsListOut,
    message_to_out,
    messages_to_out_list,
    reply_to_out,
    room_to_out,
)


async def _enrich_messages(repo: ChatRepository, items, user_id: int):
    msg_ids = [m.id for m in items]
    atts_by_msg = await repo.attachments_for_message_ids(msg_ids)
    reply_ids = [int(m.reply_to_message_id) for m in items if m.reply_to_message_id is not None]
    replies_by_id = await repo.messages_by_ids(reply_ids)
    reactions_by_msg = await repo.reactions_for_message_ids(msg_ids)
    polls_by_msg = await repo.polls_for_message_ids(msg_ids)
    poll_ids = [p.id for p in polls_by_msg.values()]
    votes_by_poll = await repo.votes_for_poll_ids(poll_ids)
    return messages_to_out_list(
        items,
        atts_by_msg,
        replies_by_id,
        reactions_by_msg,
        polls_by_msg,
        votes_by_poll,
        viewer_id=user_id,
    )


async def _room_out_for_user(repo: ChatRepository, user_id: int, room_id: int) -> RoomOut | None:
    rows = await repo.list_rooms_for_user(user_id)
    msg_ids: list[int] = []
    for row in rows:
        if row.room.id == room_id and row.last_message:
            msg_ids.append(row.last_message.id)
    polls_by_msg = await repo.polls_for_message_ids(msg_ids)
    poll_ids = [p.id for p in polls_by_msg.values()]
    votes_by_poll = await repo.votes_for_poll_ids(poll_ids)
    for row in rows:
        if row.room.id == room_id:
            return room_to_out(row, polls_by_msg=polls_by_msg, votes_by_poll=votes_by_poll, viewer_id=user_id)
    return None

router = APIRouter(prefix="/rooms", tags=["chat-rooms"])


@router.get("", response_model=RoomsListOut)
async def list_rooms(
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    rows = await repo.list_rooms_for_user(user_id)
    msg_ids = [r.last_message.id for r in rows if r.last_message]
    polls_by_msg = await repo.polls_for_message_ids(msg_ids)
    poll_ids = [p.id for p in polls_by_msg.values()]
    votes_by_poll = await repo.votes_for_poll_ids(poll_ids)
    await session.commit()
    return RoomsListOut(items=[
        room_to_out(r, polls_by_msg=polls_by_msg, votes_by_poll=votes_by_poll, viewer_id=user_id)
        for r in rows
    ])


@router.post("", response_model=RoomOut, status_code=201)
async def create_group_room(
    body: CreateGroupRoomBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    room = await repo.create_group_room(
        user_id,
        title=body.title,
        member_user_ids=body.member_user_ids,
    )
    await session.commit()
    out = await _room_out_for_user(repo, user_id, room.id)
    if out:
        recipients = await repo.member_user_ids(room.id)
        await push_chat_event(
            recipient_user_ids=[u for u in recipients if u != user_id],
            room_id=room.id,
            event="room_created",
            payload={"room_id": room.id, "room_type": room.room_type},
        )
        return out
    raise HTTPException(status_code=500, detail="Room created but not listed")


@router.post("/channel", response_model=RoomOut, status_code=201)
async def create_channel_room(
    body: CreateChannelRoomBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    room = await repo.create_channel_room(
        user_id,
        title=body.title,
        member_user_ids=body.member_user_ids,
    )
    await session.commit()
    out = await _room_out_for_user(repo, user_id, room.id)
    if out:
        recipients = await repo.member_user_ids(room.id)
        await push_chat_event(
            recipient_user_ids=[u for u in recipients if u != user_id],
            room_id=room.id,
            event="room_created",
            payload={"room_id": room.id, "room_type": room.room_type},
        )
        return out
    raise HTTPException(status_code=500, detail="Channel created but not listed")


@router.post("/dm", response_model=RoomOut, status_code=201)
async def create_or_get_dm_room(
    body: CreateDmRoomBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    room = await repo.get_or_create_dm_room(user_id, body.other_user_id)
    if not room:
        raise HTTPException(status_code=400, detail="Cannot create DM with yourself")
    await session.commit()
    out = await _room_out_for_user(repo, user_id, room.id)
    if out:
        return out
    raise HTTPException(status_code=500, detail="DM room not listed")


@router.get("/{room_id}", response_model=RoomOut)
async def get_room(
    room_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    if await repo.is_member(user_id, room_id) is None:
        raise HTTPException(status_code=404, detail="Room not found")
    room = await repo.get_room(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    await session.commit()
    out = await _room_out_for_user(repo, user_id, room_id)
    if out:
        return out
    raise HTTPException(status_code=404, detail="Room not found")


@router.get("/{room_id}/members", response_model=RoomMembersListOut)
async def list_room_members(
    room_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    members = await repo.list_members(user_id, room_id)
    if members is None:
        raise HTTPException(status_code=404, detail="Room not found")
    await session.commit()
    return RoomMembersListOut(
        items=[
            RoomMemberOut(user_id=m.user_id, role=m.role, joined_at=m.joined_at) for m in members
        ]
    )


@router.post("/{room_id}/members", response_model=RoomMembersListOut)
async def add_room_members(
    room_id: int,
    body: AddRoomMembersBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    added = await repo.add_group_members(user_id, room_id, body.user_ids)
    if added is None:
        raise HTTPException(status_code=404, detail="Room not found or not a group")
    members = await repo.list_members(user_id, room_id)
    await session.commit()
    if added:
        recipients = await repo.member_user_ids(room_id)
        await push_chat_event(
            recipient_user_ids=[u for u in recipients if u != user_id],
            room_id=room_id,
            event="members_added",
            payload={"added_user_ids": added, "room_id": room_id},
        )
    return RoomMembersListOut(
        items=[
            RoomMemberOut(user_id=m.user_id, role=m.role, joined_at=m.joined_at) for m in (members or [])
        ]
    )


@router.get("/{room_id}/messages", response_model=MessagesListOut)
async def list_messages(
    room_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
    before_id: int | None = Query(None, alias="beforeId"),
    limit: int = Query(50, ge=1, le=100),
):
    repo = ChatRepository(session)
    items = await repo.list_messages(user_id, room_id, before_id=before_id, limit=limit + 1)
    if items is None:
        raise HTTPException(status_code=404, detail="Room not found")
    has_more = len(items) > limit
    if has_more:
        items = items[:limit]
    await session.commit()
    return MessagesListOut(
        items=await _enrich_messages(repo, items, user_id),
        has_more=has_more,
    )


@router.post("/{room_id}/messages", response_model=MessageOut, status_code=201)
async def post_message(
    room_id: int,
    body: PostMessageBody,
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
    if await repo.is_member(user_id, room_id) is None:
        raise HTTPException(status_code=404, detail="Room not found")
    if not await repo.can_user_post(user_id, room_id):
        raise HTTPException(status_code=403, detail="Only admins can post in this channel")
    reply_id = body.reply_to_message_id
    if reply_id is not None:
        parent = await repo.get_message_in_room(reply_id, room_id)
        if not parent:
            raise HTTPException(status_code=400, detail="Reply target message not found in this room")
    msg = await repo.create_message(user_id, room_id, text, reply_to_message_id=reply_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Room not found")
    out = (await _enrich_messages(repo, [msg], user_id))[0]
    recipients = await repo.member_user_ids(room_id)
    await session.commit()
    await push_chat_event(
        recipient_user_ids=recipients,
        room_id=room_id,
        event="message",
        payload={"message": out.model_dump(by_alias=True, mode="json")},
    )
    return out


@router.post("/{room_id}/messages/upload", response_model=MessageOut, status_code=201)
async def post_message_with_file(
    room_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    file: UploadFile = File(...),
    body: Optional[str] = Form(None),
    reply_to_message_id: Optional[int] = Form(None, alias="replyToMessageId"),
    session: AsyncSession = Depends(get_session),
):
    settings = get_settings()
    text = (body or "").strip()
    if len(text) > settings.max_message_length:
        raise HTTPException(status_code=400, detail="Message too long")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > settings.max_file_bytes:
        mb = settings.max_file_bytes // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"File size exceeds {mb}MB")

    repo = ChatRepository(session)
    if await repo.is_member(user_id, room_id) is None:
        raise HTTPException(status_code=404, detail="Room not found")
    if not await repo.can_user_post(user_id, room_id):
        raise HTTPException(status_code=403, detail="Only admins can post in this channel")
    reply_id = reply_to_message_id
    if reply_id is not None:
        parent = await repo.get_message_in_room(reply_id, room_id)
        if not parent:
            raise HTTPException(status_code=400, detail="Reply target message not found in this room")
    msg = await repo.create_message(user_id, room_id, text, reply_to_message_id=reply_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Room not found")
    try:
        storage_key, size = save_chat_file(
            room_id=room_id,
            message_id=msg.id,
            original_filename=file.filename or "file",
            content=content,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    att = await repo.add_message_attachment(
        message_id=msg.id,
        file_name=file.filename or "file",
        content_type=file.content_type or "application/octet-stream",
        size_bytes=size,
        storage_key=storage_key,
    )
    out = (await _enrich_messages(repo, [msg], user_id))[0]
    recipients = await repo.member_user_ids(room_id)
    await session.commit()
    await push_chat_event(
        recipient_user_ids=recipients,
        room_id=room_id,
        event="message",
        payload={"message": out.model_dump(by_alias=True, mode="json")},
    )
    return out


@router.post("/{room_id}/read")
async def mark_room_read(
    room_id: int,
    body: MarkReadBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    ok = await repo.mark_read(user_id, room_id, body.message_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Room not found")
    await session.commit()
    return {"ok": True}
