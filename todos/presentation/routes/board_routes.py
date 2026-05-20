

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import get_session
from infrastructure.repositories import KanbanRepository
from infrastructure.system_notifications import notify_todo_card_assigned
from presentation.board_payload import BoardOut, build_board_out
from presentation.dependencies import get_current_user_id

router = APIRouter(prefix="/board", tags=["board"])


async def _build_board_out(session: AsyncSession, user_id: int) -> BoardOut:
    repo = KanbanRepository(session)
    board = await repo.get_last_selected_board(user_id)
    if board is None:
        board = await repo.ensure_board(user_id)
    return await build_board_out(session, board.id, viewer_user_id=user_id)


class PatchBoardBody(BaseModel):
    background_url: str | None = None


class CreateColumnBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., min_length=1, max_length=200)
    color: str = Field(default="#6b7280", max_length=32)
    insert_at: int | None = Field(
        None,
        description="Индекс вставки (0 — начало); по умолчанию в конец",
    )
    is_collapsed: bool = Field(False, alias="isCollapsed")


class PatchColumnBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str | None = Field(None, min_length=1, max_length=200)
    color: str | None = Field(None, max_length=32)
    is_collapsed: bool | None = Field(None, alias="isCollapsed")


class ReorderColumnsBody(BaseModel):
    ordered_column_ids: list[int] = Field(..., min_length=1)


class CreateCardBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., min_length=1, max_length=500)
    body: str | None = None
    insert_at: int | None = Field(
        None,
        description="Индекс в колонке; по умолчанию в конец",
    )
    due_at: datetime | None = Field(None, alias="dueAt")


class PatchCardBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str | None = Field(None, min_length=1, max_length=500)
    body: str | None = None
    column_id: int | None = Field(None, alias="columnId")
    position: int | None = Field(
        None,
        description="Позиция в колонке (после смены column_id — в целевой колонке)",
    )
    due_at: datetime | None = Field(None, alias="dueAt")
    is_completed: bool | None = Field(None, alias="isCompleted")
    is_archived: bool | None = Field(None, alias="isArchived")
    label_ids: list[int] | None = Field(None, alias="labelIds")
    participant_user_ids: list[int] | None = Field(None, alias="participantUserIds")


class ReorderCardsBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ordered_card_ids: list[int] = Field(..., min_length=1, alias="orderedCardIds")


class CreateBoardLabelBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., min_length=1, max_length=200)
    color: str = Field(default="#6b7280", max_length=32)


class PatchBoardLabelBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str | None = Field(None, min_length=1, max_length=200)
    color: str | None = Field(None, max_length=32)


class CreateChecklistItemBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., min_length=1, max_length=500)
    insert_at: int | None = Field(
        None,
        description="Индекс в чеклисте; по умолчанию в конец",
    )


class PatchChecklistItemBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str | None = Field(None, min_length=1, max_length=500)
    is_done: bool | None = Field(None, alias="isDone")


class ReorderChecklistBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ordered_item_ids: list[int] = Field(..., min_length=1, alias="orderedItemIds")


class CreateCommentBody(BaseModel):
    body: str = Field(..., min_length=1)


@router.get("", response_model=BoardOut)
async def get_board(
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):

    repo = KanbanRepository(session)
    board = await repo.ensure_board(user_id)
    if await repo.get_last_selected_board_id(user_id) is None:
        await repo.set_last_selected_board_id(user_id, board.id)
    await session.commit()
    return await _build_board_out(session, user_id)


@router.patch("", response_model=BoardOut)
async def patch_board(
    body: PatchBoardBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    repo = KanbanRepository(session)
    await repo.patch_board(
        user_id,
        background_url=data.get("background_url"),
    )
    await session.commit()
    return await _build_board_out(session, user_id)


@router.post("/labels", response_model=BoardOut)
async def create_board_label(
    body: CreateBoardLabelBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    row = await repo.add_board_label(user_id, title=body.title, color=body.color)
    if not row:
        raise HTTPException(status_code=404, detail="Board not found")
    await session.commit()
    return await _build_board_out(session, user_id)


@router.patch("/labels/{label_id}", response_model=BoardOut)
async def patch_board_label(
    label_id: int,
    body: PatchBoardLabelBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")
    repo = KanbanRepository(session)
    row = await repo.update_board_label(
        user_id,
        label_id,
        title=patch.get("title"),
        color=patch.get("color"),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Label not found")
    await session.commit()
    return await _build_board_out(session, user_id)


@router.delete("/labels/{label_id}", response_model=BoardOut)
async def delete_board_label(
    label_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    ok = await repo.delete_board_label(user_id, label_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Label not found")
    await session.commit()
    return await _build_board_out(session, user_id)


@router.post("/columns", response_model=BoardOut)
async def create_column(
    body: CreateColumnBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    col = await repo.add_column(
        user_id,
        title=body.title,
        color=body.color,
        insert_at=body.insert_at,
        is_collapsed=body.is_collapsed,
    )
    if not col:
        raise HTTPException(status_code=404, detail="Board not found")
    await session.commit()
    return await _build_board_out(session, user_id)


@router.patch("/columns/{column_id}", response_model=BoardOut)
async def patch_column(
    column_id: int,
    body: PatchColumnBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    patch = body.model_dump(exclude_unset=True, by_alias=False)
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")
    repo = KanbanRepository(session)
    col = await repo.update_column(
        user_id,
        column_id,
        title=patch.get("title"),
        color=patch.get("color"),
        is_collapsed=patch.get("is_collapsed"),
    )
    if not col:
        raise HTTPException(status_code=404, detail="Column not found")
    await session.commit()
    return await _build_board_out(session, user_id)


@router.delete("/columns/{column_id}", response_model=BoardOut)
async def delete_column(
    column_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    ok = await repo.delete_column(user_id, column_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Column not found")
    await session.commit()
    return await _build_board_out(session, user_id)


@router.put("/columns/reorder", response_model=BoardOut)
async def reorder_columns(
    body: ReorderColumnsBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    ok = await repo.reorder_columns(user_id, body.ordered_column_ids)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Invalid column id list (must match all columns on the board)",
        )
    await session.commit()
    return await _build_board_out(session, user_id)


@router.post("/columns/{column_id}/cards", response_model=BoardOut)
async def create_card(
    column_id: int,
    body: CreateCardBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    card = await repo.add_card(
        user_id,
        column_id,
        title=body.title,
        body=body.body,
        insert_at=body.insert_at,
        due_at=body.due_at,
    )
    if not card:
        raise HTTPException(status_code=404, detail="Column not found")
    await session.commit()
    return await _build_board_out(session, user_id)


@router.post("/cards/{card_id}/checklist/items", response_model=BoardOut)
async def create_checklist_item(
    card_id: int,
    body: CreateChecklistItemBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    row = await repo.add_checklist_item(
        user_id,
        card_id,
        title=body.title,
        insert_at=body.insert_at,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Card not found")
    await session.commit()
    return await _build_board_out(session, user_id)


@router.patch("/cards/{card_id}/checklist/items/{item_id}", response_model=BoardOut)
async def patch_checklist_item(
    card_id: int,
    item_id: int,
    body: PatchChecklistItemBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    patch = body.model_dump(exclude_unset=True, by_alias=False)
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")
    repo = KanbanRepository(session)
    row = await repo.update_checklist_item(
        user_id,
        card_id,
        item_id,
        title=patch.get("title"),
        is_done=patch.get("is_done"),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Checklist item not found")
    await session.commit()
    return await _build_board_out(session, user_id)


@router.delete("/cards/{card_id}/checklist/items/{item_id}", response_model=BoardOut)
async def delete_checklist_item(
    card_id: int,
    item_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    ok = await repo.delete_checklist_item(user_id, card_id, item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Checklist item not found")
    await session.commit()
    return await _build_board_out(session, user_id)


@router.put("/cards/{card_id}/checklist/reorder", response_model=BoardOut)
async def reorder_checklist(
    card_id: int,
    body: ReorderChecklistBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    ok = await repo.reorder_checklist_items(user_id, card_id, body.ordered_item_ids)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Invalid checklist item id list",
        )
    await session.commit()
    return await _build_board_out(session, user_id)


@router.post("/cards/{card_id}/attachments", response_model=BoardOut)
async def upload_card_attachment(
    card_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
    file: UploadFile = File(..., description="Тело multipart, поле формы «file»"),
):
    content = await file.read()
    repo = KanbanRepository(session)
    try:
        row = await repo.add_card_attachment(
            user_id,
            card_id,
            original_filename=file.filename or "file",
            content=content,
            mime_type=file.content_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    if not row:
        raise HTTPException(status_code=404, detail="Card not found")
    await session.commit()
    return await _build_board_out(session, user_id)


@router.delete("/cards/{card_id}/attachments/{attachment_id}", response_model=BoardOut)
async def delete_card_attachment(
    card_id: int,
    attachment_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    ok = await repo.delete_card_attachment(user_id, card_id, attachment_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Attachment not found")
    await session.commit()
    return await _build_board_out(session, user_id)


@router.post("/cards/{card_id}/comments", response_model=BoardOut)
async def create_card_comment(
    card_id: int,
    body: CreateCommentBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    row = await repo.add_card_comment(user_id, card_id, body=body.body)
    if not row:
        raise HTTPException(status_code=404, detail="Card not found")
    await session.commit()
    return await _build_board_out(session, user_id)


@router.patch("/cards/{card_id}", response_model=BoardOut)
async def patch_card(
    card_id: int,
    body: PatchCardBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    patch = body.model_dump(exclude_unset=True, by_alias=False)
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")
    repo = KanbanRepository(session)
    if "label_ids" in patch:
        ok = await repo.replace_card_labels(user_id, card_id, patch["label_ids"] or [])
        if not ok:
            raise HTTPException(
                status_code=400,
                detail="Invalid label_ids (must belong to this board)",
            )
    newly_added_participant_ids: list[int] = []
    if "participant_user_ids" in patch:
        current_map = await repo.batch_participant_ids([card_id])
        current_ids = set(current_map.get(card_id, []))
        next_ids = {int(x) for x in (patch["participant_user_ids"] or [])}
        newly_added_participant_ids = sorted(next_ids - current_ids - {int(user_id)})
        await repo.replace_card_participants(
            user_id,
            card_id,
            patch["participant_user_ids"] or [],
        )
    due_at_provided = "due_at" in patch
    card = await repo.update_card(
        user_id,
        card_id,
        title=patch.get("title"),
        body=patch.get("body"),
        new_column_id=patch.get("column_id"),
        new_position=patch.get("position"),
        due_at=patch.get("due_at"),
        due_at_provided=due_at_provided,
        is_completed=patch.get("is_completed"),
        is_archived=patch.get("is_archived"),
    )
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    col = await repo.get_column_if_owned(user_id, card.column_id, need_write=False)
    board_id = col.board_id if col else None
    await session.commit()
    if board_id is not None:
        for recipient_user_id in newly_added_participant_ids:
            await notify_todo_card_assigned(
                recipient_user_id=recipient_user_id,
                actor_user_id=user_id,
                board_id=board_id,
                card_id=card_id,
                card_title=card.title,
            )
    return await _build_board_out(session, user_id)


@router.delete("/cards/{card_id}", response_model=BoardOut)
async def delete_card(
    card_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    ok = await repo.delete_card(user_id, card_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Card not found")
    await session.commit()
    return await _build_board_out(session, user_id)


@router.put("/columns/{column_id}/cards/reorder", response_model=BoardOut)
async def reorder_cards(
    column_id: int,
    body: ReorderCardsBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    ok = await repo.reorder_cards_in_column(user_id, column_id, body.ordered_card_ids)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Invalid card id list (must match all cards in the column)",
        )
    await session.commit()
    return await _build_board_out(session, user_id)
