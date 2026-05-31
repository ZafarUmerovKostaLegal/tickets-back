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
    room_to_out,
)

router = APIRouter(prefix="/rooms", tags=["chat-rooms"])


@router.get("", response_model=RoomsListOut)
async def list_rooms(
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    rows = await repo.list_rooms_for_user(user_id)
    await session.commit()
    return RoomsListOut(items=[room_to_out(r) for r in rows])


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
    rows = await repo.list_rooms_for_user(user_id)
    for row in rows:
        if row.room.id == room.id:
            return room_to_out(row)
    raise HTTPException(status_code=500, detail="Room created but not listed")


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
    rows = await repo.list_rooms_for_user(user_id)
    for row in rows:
        if row.room.id == room.id:
            return room_to_out(row)
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
    rows = await repo.list_rooms_for_user(user_id)
    await session.commit()
    for row in rows:
        if row.room.id == room_id:
            return room_to_out(row)
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
    atts_by_msg = await repo.attachments_for_message_ids([m.id for m in items])
    await session.commit()
    return MessagesListOut(
        items=[message_to_out(m, atts_by_msg.get(m.id)) for m in items],
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
    msg = await repo.create_message(user_id, room_id, text)
    if not msg:
        raise HTTPException(status_code=404, detail="Room not found")
    out = message_to_out(msg)
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
    msg = await repo.create_message(user_id, room_id, text)
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
    out = message_to_out(msg, [att])
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
