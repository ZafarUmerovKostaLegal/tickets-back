from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import get_session
from infrastructure.realtime_push import push_chat_event
from infrastructure.repositories import ChatRepository
from presentation.dependencies import get_current_user_id
from presentation.schemas import PinMessageBody, PinsListOut, pins_to_out

router = APIRouter(tags=["chat-pins"])


async def _pins_out(repo: ChatRepository, room_id: int, user_id: int) -> PinsListOut:
    rows = await repo.list_room_pins(room_id)
    can_pin = await repo.viewer_can_pin(user_id, room_id)
    return pins_to_out(rows, can_pin=can_pin)


async def _push_pins(repo: ChatRepository, room_id: int) -> None:
    rows = await repo.list_room_pins(room_id)
    out = pins_to_out(rows, can_pin=False)
    recipients = await repo.member_user_ids(room_id)
    await push_chat_event(
        recipient_user_ids=recipients,
        room_id=room_id,
        event="pins_updated",
        payload={"items": [item.model_dump(by_alias=True, mode="json") for item in out.items]},
    )


@router.get("/rooms/{room_id}/pins", response_model=PinsListOut)
async def list_pins(
    room_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    if await repo.is_member(user_id, room_id) is None:
        raise HTTPException(status_code=404, detail="Чат не найден")
    out = await _pins_out(repo, room_id, user_id)
    await session.commit()
    return out


@router.post("/rooms/{room_id}/pins", response_model=PinsListOut)
async def pin_message(
    room_id: int,
    body: PinMessageBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    status, rows = await repo.pin_message(user_id, room_id, body.message_id)
    if status == "limit":
        raise HTTPException(status_code=400, detail="Можно закрепить не больше 20 сообщений")
    if status != "ok":
        raise HTTPException(status_code=400, detail="Нельзя закрепить это сообщение")
    can_pin = await repo.viewer_can_pin(user_id, room_id)
    out = pins_to_out(rows, can_pin=can_pin)
    await session.commit()
    await _push_pins(repo, room_id)
    return out


@router.delete("/rooms/{room_id}/pins/{message_id}", response_model=PinsListOut)
async def unpin_message(
    room_id: int,
    message_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    rows = await repo.unpin_message(user_id, room_id, message_id)
    if rows is None:
        raise HTTPException(status_code=400, detail="Нельзя открепить это сообщение")
    can_pin = await repo.viewer_can_pin(user_id, room_id)
    out = pins_to_out(rows, can_pin=can_pin)
    await session.commit()
    await _push_pins(repo, room_id)
    return out
