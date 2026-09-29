from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import get_session
from infrastructure.realtime_push import push_chat_event
from infrastructure.repositories import ChatRepository
from presentation.dependencies import get_current_user_id
from presentation.routes.rooms_routes import _enrich_messages
from presentation.schemas import AppendChecklistTaskBody, CreateChecklistBody, MessageOut

router = APIRouter(tags=["chat-checklists"])


@router.post("/rooms/{room_id}/checklists", response_model=MessageOut, status_code=201)
async def create_checklist(
    room_id: int,
    body: CreateChecklistBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    result = await repo.create_checklist_message(
        user_id,
        room_id,
        title=body.title,
        tasks=body.tasks,
        others_can_complete=body.others_can_complete,
        others_can_append=body.others_can_append,
    )
    if not result:
        raise HTTPException(status_code=400, detail="Чеклист можно создать только в группе или личном чате")
    msg, _checklist = result
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


@router.post("/checklists/{checklist_id}/items/{item_id}/toggle", response_model=MessageOut)
async def toggle_checklist_item(
    checklist_id: int,
    item_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    msg = await repo.toggle_checklist_item(user_id, checklist_id, item_id)
    if not msg:
        raise HTTPException(status_code=400, detail="Нельзя изменить эту задачу")
    out = (await _enrich_messages(repo, [msg], user_id))[0]
    recipients = await repo.member_user_ids(msg.room_id)
    await session.commit()
    await push_chat_event(
        recipient_user_ids=recipients,
        room_id=msg.room_id,
        event="message",
        payload={"message": out.model_dump(by_alias=True, mode="json")},
    )
    return out


@router.post("/checklists/{checklist_id}/tasks", response_model=MessageOut)
async def append_checklist_task(
    checklist_id: int,
    body: AppendChecklistTaskBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    msg = await repo.append_checklist_task(user_id, checklist_id, body.text)
    if not msg:
        raise HTTPException(status_code=400, detail="Нельзя добавить задачу")
    out = (await _enrich_messages(repo, [msg], user_id))[0]
    recipients = await repo.member_user_ids(msg.room_id)
    await session.commit()
    await push_chat_event(
        recipient_user_ids=recipients,
        room_id=msg.room_id,
        event="message",
        payload={"message": out.model_dump(by_alias=True, mode="json")},
    )
    return out


@router.delete("/checklists/{checklist_id}/items/{item_id}", response_model=MessageOut)
async def remove_checklist_task(
    checklist_id: int,
    item_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = ChatRepository(session)
    msg = await repo.remove_checklist_task(user_id, checklist_id, item_id)
    if not msg:
        raise HTTPException(status_code=400, detail="Нельзя удалить эту задачу")
    out = (await _enrich_messages(repo, [msg], user_id))[0]
    recipients = await repo.member_user_ids(msg.room_id)
    await session.commit()
    await push_chat_event(
        recipient_user_ids=recipients,
        room_id=msg.room_id,
        event="message",
        payload={"message": out.model_dump(by_alias=True, mode="json")},
    )
    return out
