from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import TODO_BOARD_TITLE_NEW_DEFAULT, TODO_BOARD_TITLE_PRIMARY_DEFAULT
from infrastructure.repositories import KanbanRepository


def media_url(storage_key: str) -> str:
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
    title: str = TODO_BOARD_TITLE_PRIMARY_DEFAULT
    visibility: str = "personal"
    color: str | None = None
    background_url: str | None
    board_labels: list[BoardLabelOut]
    columns: list[ColumnOut]


class BoardSummaryOut(BaseModel):
    id: int
    title: str
    visibility: str
    color: str | None = None
    background_url: str | None = None
    sort_order: int = 0
    is_current: bool = False
    updated_at: datetime | None = None
    my_role: str | None = None


class BoardsListOut(BaseModel):
    items: list[BoardSummaryOut]
    current_board_id: int | None = None
    last_selected_board_id: int | None = None


class BoardMemberOut(BaseModel):
    user_id: int
    role: str
    joined_at: datetime


class BoardMembersListOut(BaseModel):
    items: list[BoardMemberOut]


class BoardInviteOut(BaseModel):
    id: int
    board_id: int
    board_title: str
    inviter_user_id: int
    role_offered: str
    status: str
    message: str | None = None
    created_at: datetime
    expires_at: datetime


class BoardInvitesListOut(BaseModel):
    items: list[BoardInviteOut]


class PatchBoardBodyFull(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    background_url: str | None = Field(None, alias="backgroundUrl")
    title: str | None = Field(None, min_length=1, max_length=200)
    color: str | None = Field(None, max_length=32)
    visibility: str | None = None


class CreateBoardBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(
        default=TODO_BOARD_TITLE_NEW_DEFAULT,
        min_length=1,
        max_length=200,
        description="Название доски. Новая доска создаётся с тремя пустыми колонками и своим фоном (background_url).",
    )
    visibility: str = Field(default="personal")
    color: str | None = Field(None, max_length=32)
    member_user_ids: list[int] = Field(default_factory=list, alias="memberUserIds")
    instant_add_members: bool = Field(default=False, alias="instantAddMembers")


class SelectBoardBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    board_id: int = Field(..., alias="boardId")


class CreateBoardInvitesBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_ids: list[int] = Field(..., min_length=1)
    role: str = Field(default="editor")
    message: str | None = Field(None, max_length=500)


class PatchBoardMemberBody(BaseModel):
    role: str = Field(default="editor")


class AddBoardMembersBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_ids: list[int] = Field(..., min_length=1, alias="userIds")
    role: str = Field(default="editor")
    instant: bool = Field(
        default=True,
        description="true — сразу в участники; false — приглашения (как instantAddMembers при создании доски)",
    )


async def build_board_out(
    session: AsyncSession,
    board_id: int,
    *,
    viewer_user_id: int | None = None,
) -> BoardOut:
    repo = KanbanRepository(session)
    board = await repo.get_board_by_id(board_id)
    if not board:
        raise ValueError("board not found")
    viewer_role = await repo.board_role(viewer_user_id, board_id) if viewer_user_id else None
    board_label_rows = await repo.list_board_labels_for_board(board_id)
    board_labels = [
        BoardLabelOut(id=r.id, title=r.title, color=r.color, position=r.position)
        for r in board_label_rows
    ]
    cols = await repo._columns_for_board(board.id)
    out_cols: list[ColumnOut] = []
    for col in cols:
        cards = await repo._cards_for_column(col.id)
        if viewer_user_id is not None and viewer_role == "participant":
            part_map_for_filter = await repo.batch_participant_ids([c.id for c in cards])
            cards = [
                c
                for c in cards
                if int(viewer_user_id) in set(part_map_for_filter.get(c.id, []))
            ]
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
                    media_url=media_url(a.storage_key),
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
        title=board.title,
        visibility=board.visibility,
        color=board.color,
        background_url=board.background_url,
        board_labels=board_labels,
        columns=out_cols,
    )
