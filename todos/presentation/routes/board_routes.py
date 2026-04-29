

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import get_session
from infrastructure.repositories import KanbanRepository
from presentation.dependencies import get_current_user_id

router = APIRouter(prefix="/board", tags=["board"])


def _media_url(storage_key: str) -> str:
    return f"/api/v1/media/{storage_key}"


class BoardLabelOut(BaseModel):
    id: int
    title: str
    color: str
    position: int


class CardLabelOut(BaseModel):
    id: int
    title: str
    color: str


class ChecklistItemOut(BaseModel):
    id: int
    title: str
    is_done: bool
    position: int


class AttachmentOut(BaseModel):
    id: int
    original_filename: str
    mime_type: str | None
    size_bytes: int
    media_url: str


class CommentOut(BaseModel):
    id: int
    user_id: int
    body: str
    created_at: datetime


class CardOut(BaseModel):
    id: int
    title: str
    body: str | None
    position: int
    created_at: datetime
    due_at: datetime | None
    is_completed: bool
    is_archived: bool
    labels: list[CardLabelOut]
    checklist: list[ChecklistItemOut]
    participant_user_ids: list[int]
    attachments: list[AttachmentOut]
    comments: list[CommentOut]


class ColumnOut(BaseModel):
    id: int
    title: str
    position: int
    color: str
    is_collapsed: bool = False
    task_count: int
    cards: list[CardOut]


class BoardOut(BaseModel):
    id: int
    user_id: int
    background_url: str | None
    board_labels: list[BoardLabelOut]
    columns: list[ColumnOut]


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


async def _build_board_out(
    session: AsyncSession,
    user_id: int,
) -> BoardOut:
    repo = KanbanRepository(session)
    board = await repo.ensure_board(user_id)
    board_label_rows = await repo.list_board_labels(user_id)
    board_labels = [
        BoardLabelOut(id=r.id, title=r.title, color=r.color, position=r.position)
        for r in board_label_rows
    ]
    cols = await repo._columns_for_board(board.id)
    out_cols: list[ColumnOut] = []
    for col in cols:
        cards = await repo._cards_for_column(col.id)
        card_ids = [c.id for c in cards]
        lbl_map = await repo.batch_card_label_payload(card_ids)
        chk_map = await repo.batch_checklist_items(card_ids)
        part_map = await repo.batch_participant_ids(card_ids)
        att_map = await repo.batch_attachments(card_ids)
        com_map = await repo.batch_comments(card_ids)
        card_outs: list[CardOut] = []
        for c in cards:
            labels_raw = lbl_map.get(c.id, [])
            labels = [
                CardLabelOut(id=lid, title=title, color=color)
                for lid, title, color in labels_raw
            ]
            checklist = [
                ChecklistItemOut(
                    id=it.id,
                    title=it.title,
                    is_done=it.is_done,
                    position=it.position,
                )
                for it in chk_map.get(c.id, [])
            ]
            attachments = [
                AttachmentOut(
                    id=a.id,
                    original_filename=a.original_filename,
                    mime_type=a.mime_type,
                    size_bytes=a.size_bytes,
                    media_url=_media_url(a.storage_key),
                )
                for a in att_map.get(c.id, [])
            ]
            comments = [
                CommentOut(
                    id=cm.id,
                    user_id=cm.user_id,
                    body=cm.body,
                    created_at=cm.created_at,
                )
                for cm in com_map.get(c.id, [])
            ]
            card_outs.append(
                CardOut(
                    id=c.id,
                    title=c.title,
                    body=c.body,
                    position=c.position,
                    created_at=c.created_at,
                    due_at=c.due_at,
                    is_completed=c.is_completed,
                    is_archived=c.is_archived,
                    labels=labels,
                    checklist=checklist,
                    participant_user_ids=part_map.get(c.id, []),
                    attachments=attachments,
                    comments=comments,
                )
            )
        out_cols.append(
            ColumnOut(
                id=col.id,
                title=col.title,
                position=col.position,
                color=col.color,
                is_collapsed=col.is_collapsed,
                task_count=len(cards),
                cards=card_outs,
            )
        )
    return BoardOut(
        id=board.id,
        user_id=board.user_id,
        background_url=board.background_url,
        board_labels=board_labels,
        columns=out_cols,
    )


@router.get("", response_model=BoardOut)
async def get_board(
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):

    repo = KanbanRepository(session)
    await repo.ensure_board(user_id)
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
    await repo.add_board_label(user_id, title=body.title, color=body.color)
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
    await repo.add_column(
        user_id,
        title=body.title,
        color=body.color,
        insert_at=body.insert_at,
        is_collapsed=body.is_collapsed,
    )
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
    if "participant_user_ids" in patch:
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
    await session.commit()
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
