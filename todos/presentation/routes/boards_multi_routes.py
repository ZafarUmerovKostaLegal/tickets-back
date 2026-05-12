from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import get_session
from infrastructure.models import BOARD_VIS_SHARED, TodoBoardLabelModel, TodoColumnModel
from infrastructure.repositories import KanbanRepository
from presentation.board_payload import (
    BoardInvitesListOut,
    BoardInviteOut,
    BoardMembersListOut,
    BoardMemberOut,
    BoardOut,
    BoardsListOut,
    BoardSummaryOut,
    CreateBoardBody,
    CreateBoardInvitesBody,
    PatchBoardBodyFull,
    PatchBoardMemberBody,
    build_board_out,
)
from presentation.dependencies import get_current_user_id
from presentation.routes.board_routes import (
    CreateBoardLabelBody,
    CreateCardBody,
    CreateChecklistItemBody,
    CreateColumnBody,
    CreateCommentBody,
    PatchBoardLabelBody,
    PatchCardBody,
    PatchChecklistItemBody,
    PatchColumnBody,
    ReorderChecklistBody,
    ReorderCardsBody,
    ReorderColumnsBody,
)

boards_router = APIRouter(prefix="/boards", tags=["boards"])
invites_router = APIRouter(prefix="/invites", tags=["invites"])


async def _require_read(session: AsyncSession, user_id: int, board_id: int) -> None:
    repo = KanbanRepository(session)
    if await repo.require_board_read(user_id, board_id) is None:
        raise HTTPException(status_code=404, detail="Board not found")


async def _require_write(session: AsyncSession, user_id: int, board_id: int) -> None:
    repo = KanbanRepository(session)
    if await repo.require_board_write(user_id, board_id) is None:
        raise HTTPException(status_code=404, detail="Board not found")


@boards_router.get("", response_model=BoardsListOut)
async def list_boards(
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    primary = await repo.get_primary_owned_board(user_id)
    primary_id = primary.id if primary else None
    rows = await repo.list_accessible_boards_with_roles(user_id)
    items: list[BoardSummaryOut] = []
    for b, role in rows:
        items.append(
            BoardSummaryOut(
                id=b.id,
                title=b.title,
                visibility=b.visibility,
                color=b.color,
                sort_order=b.sort_order,
                is_current=(b.id == primary_id),
                updated_at=b.updated_at,
                my_role=role,
            )
        )
    await session.commit()
    return BoardsListOut(items=items)


@boards_router.get("/current", response_model=BoardOut)
async def get_current_board(
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    board = await repo.ensure_board(user_id)
    await session.commit()
    return await build_board_out(session, board.id)


@boards_router.post("", response_model=BoardOut)
async def create_board(
    body: CreateBoardBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    vis = body.visibility.strip()
    if vis not in ("personal", "shared"):
        raise HTTPException(status_code=400, detail="Invalid visibility")
    if vis == "personal" and body.member_user_ids:
        pass
    repo = KanbanRepository(session)
    try:
        board = await repo.create_board(
            user_id,
            title=body.title,
            visibility=vis,
            color=body.color,
            member_user_ids=body.member_user_ids if vis == BOARD_VIS_SHARED else [],
            instant_add_members=body.instant_add_members,
        )
    except IntegrityError as exc:
        await session.rollback()
        orig = getattr(exc, "orig", None)
        pg_hint = (str(orig).strip() if orig else "") or str(exc).strip()
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Could not create board",
                "hint": (
                    "Частая причина: в PostgreSQL всё ещё действует UNIQUE(user_id) на таблице "
                    "todo_boards (старая схема). Перезапустите сервис todos, чтобы применился патч "
                    "apply_todo_boards_multi_user_patch, или вручную снимите уникальность с user_id."
                ),
                "postgres": pg_hint[:2000],
            },
        ) from exc
    await session.commit()
    return await build_board_out(session, board.id)


@boards_router.get("/{board_id}", response_model=BoardOut)
async def get_board_by_id(
    board_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    await _require_read(session, user_id, board_id)
    await session.commit()
    return await build_board_out(session, board_id)


@boards_router.patch("/{board_id}", response_model=BoardOut)
async def patch_board_by_id(
    board_id: int,
    body: PatchBoardBodyFull,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    patch = body.model_dump(exclude_unset=True, by_alias=True)
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")
    repo = KanbanRepository(session)
    background_url_set = "backgroundUrl" in patch
    bg = patch.get("backgroundUrl")
    title = patch.get("title")
    color = patch.get("color")
    visibility = patch.get("visibility")
    if visibility is not None and visibility not in ("personal", "shared"):
        raise HTTPException(status_code=400, detail="Invalid visibility")
    row = await repo.patch_board_meta(
        user_id,
        board_id,
        title=title,
        color=color,
        visibility=visibility,
        background_url=bg,
        background_url_set=background_url_set,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Board not found")
    await session.commit()
    return await build_board_out(session, board_id)


@boards_router.delete("/{board_id}")
async def delete_board_by_id(
    board_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    ok = await repo.archive_board(user_id, board_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Board not found")
    await session.commit()
    return {"ok": True}


@boards_router.get("/{board_id}/members", response_model=BoardMembersListOut)
async def list_members(
    board_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    b = await repo.require_board_read(user_id, board_id)
    if not b:
        raise HTTPException(status_code=404, detail="Board not found")
    members = await repo.list_board_members(user_id, board_id)
    if members is None:
        raise HTTPException(status_code=404, detail="Board not found")
    items = [
        BoardMemberOut(user_id=b.user_id, role="owner", joined_at=b.created_at),
    ]
    items.extend(
        BoardMemberOut(user_id=m.user_id, role=m.role, joined_at=m.joined_at)
        for m in members
    )
    await session.commit()
    return BoardMembersListOut(items=items)


@boards_router.patch("/{board_id}/members/{member_user_id}", response_model=BoardMembersListOut)
async def patch_member_role(
    board_id: int,
    member_user_id: int,
    body: PatchBoardMemberBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    if body.role not in ("editor", "viewer"):
        raise HTTPException(status_code=400, detail="Invalid role")
    repo = KanbanRepository(session)
    row = await repo.patch_board_member_role(
        user_id,
        board_id,
        member_user_id,
        role=body.role,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Member not found")
    await session.commit()
    return await list_members(board_id, user_id, session)


@boards_router.delete("/{board_id}/members/{member_user_id}", response_model=BoardMembersListOut)
async def remove_member(
    board_id: int,
    member_user_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    ok = await repo.remove_board_member(user_id, board_id, member_user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Member not found")
    await session.commit()
    return await list_members(board_id, user_id, session)


@boards_router.post("/{board_id}/invites", response_model=BoardInvitesListOut)
async def post_invites(
    board_id: int,
    body: CreateBoardInvitesBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    if body.role not in ("editor", "viewer"):
        raise HTTPException(status_code=400, detail="Invalid role")
    repo = KanbanRepository(session)
    created = await repo.create_board_invites(
        user_id,
        board_id,
        invitee_ids=body.user_ids,
        role=body.role,
        message=body.message,
    )
    if created is None:
        raise HTTPException(status_code=404, detail="Board not found")
    b = await repo.get_board_by_id(board_id)
    assert b
    await session.commit()
    items = [
        BoardInviteOut(
            id=inv.id,
            board_id=inv.board_id,
            board_title=b.title,
            inviter_user_id=inv.inviter_user_id,
            role_offered=inv.role_offered,
            status=inv.status,
            message=inv.message,
            created_at=inv.created_at,
            expires_at=inv.expires_at,
        )
        for inv in created
    ]
    return BoardInvitesListOut(items=items)


@boards_router.get("/{board_id}/invites", response_model=BoardInvitesListOut)
async def list_board_invites(
    board_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    invs = await repo.list_pending_invites_for_board(user_id, board_id)
    b = await repo.get_board_by_id(board_id)
    if not b:
        raise HTTPException(status_code=404, detail="Board not found")
    items = [
        BoardInviteOut(
            id=inv.id,
            board_id=inv.board_id,
            board_title=b.title,
            inviter_user_id=inv.inviter_user_id,
            role_offered=inv.role_offered,
            status=inv.status,
            message=inv.message,
            created_at=inv.created_at,
            expires_at=inv.expires_at,
        )
        for inv in invs
    ]
    await session.commit()
    return BoardInvitesListOut(items=items)


@invites_router.get("", response_model=BoardInvitesListOut)
async def list_my_invites(
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    rows = await repo.list_pending_invites_for_user(user_id)
    items = [
        BoardInviteOut(
            id=inv.id,
            board_id=inv.board_id,
            board_title=board.title,
            inviter_user_id=inv.inviter_user_id,
            role_offered=inv.role_offered,
            status=inv.status,
            message=inv.message,
            created_at=inv.created_at,
            expires_at=inv.expires_at,
        )
        for inv, board in rows
    ]
    await session.commit()
    return BoardInvitesListOut(items=items)


@invites_router.post("/{invite_id}/accept", response_model=BoardOut)
async def accept_invite(
    invite_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    inv = await repo.accept_invite(invite_id, user_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invite not found")
    bid = inv.board_id
    await session.commit()
    return await build_board_out(session, bid)


@invites_router.post("/{invite_id}/decline")
async def decline_invite(
    invite_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    ok = await repo.decline_invite(invite_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Invite not found")
    await session.commit()
    return {"ok": True}


@invites_router.post("/{invite_id}/revoke")
async def revoke_invite(
    invite_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = KanbanRepository(session)
    ok = await repo.revoke_invite(invite_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Invite not found")
    await session.commit()
    return {"ok": True}


@boards_router.post("/{board_id}/labels", response_model=BoardOut)
async def create_label_nested(
    board_id: int,
    body: CreateBoardLabelBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    await _require_write(session, user_id, board_id)
    repo = KanbanRepository(session)
    row = await repo.add_board_label(user_id, title=body.title, color=body.color, board_id=board_id)
    if not row:
        raise HTTPException(status_code=404, detail="Board not found")
    await session.commit()
    return await build_board_out(session, board_id)


@boards_router.patch("/{board_id}/labels/{label_id}", response_model=BoardOut)
async def patch_label_nested(
    board_id: int,
    label_id: int,
    body: PatchBoardLabelBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    await _require_write(session, user_id, board_id)
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")
    repo = KanbanRepository(session)
    row = await repo.update_board_label(user_id, label_id, title=patch.get("title"), color=patch.get("color"))
    if not row or row.board_id != board_id:
        raise HTTPException(status_code=404, detail="Label not found")
    await session.commit()
    return await build_board_out(session, board_id)


@boards_router.delete("/{board_id}/labels/{label_id}", response_model=BoardOut)
async def delete_label_nested(
    board_id: int,
    label_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    await _require_write(session, user_id, board_id)
    r = await session.execute(
        select(TodoBoardLabelModel).where(
            TodoBoardLabelModel.id == label_id,
            TodoBoardLabelModel.board_id == board_id,
        )
    )
    if r.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Label not found")
    repo = KanbanRepository(session)
    ok = await repo.delete_board_label(user_id, label_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Label not found")
    await session.commit()
    return await build_board_out(session, board_id)


@boards_router.post("/{board_id}/columns", response_model=BoardOut)
async def create_column_nested(
    board_id: int,
    body: CreateColumnBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    await _require_write(session, user_id, board_id)
    repo = KanbanRepository(session)
    col = await repo.add_column(
        user_id,
        board_id=board_id,
        title=body.title,
        color=body.color,
        insert_at=body.insert_at,
        is_collapsed=body.is_collapsed,
    )
    if not col:
        raise HTTPException(status_code=404, detail="Board not found")
    await session.commit()
    return await build_board_out(session, board_id)


@boards_router.patch("/{board_id}/columns/{column_id}", response_model=BoardOut)
async def patch_column_nested(
    board_id: int,
    column_id: int,
    body: PatchColumnBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    await _require_write(session, user_id, board_id)
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
    if not col or col.board_id != board_id:
        raise HTTPException(status_code=404, detail="Column not found")
    await session.commit()
    return await build_board_out(session, board_id)


@boards_router.delete("/{board_id}/columns/{column_id}", response_model=BoardOut)
async def delete_column_nested(
    board_id: int,
    column_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    await _require_write(session, user_id, board_id)
    repo = KanbanRepository(session)
    col = await repo.get_column_if_owned(user_id, column_id, need_write=True)
    if not col or col.board_id != board_id:
        raise HTTPException(status_code=404, detail="Column not found")
    ok = await repo.delete_column(user_id, column_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Column not found")
    await session.commit()
    return await build_board_out(session, board_id)


@boards_router.put("/{board_id}/columns/reorder", response_model=BoardOut)
async def reorder_columns_nested(
    board_id: int,
    body: ReorderColumnsBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    if not body.ordered_column_ids:
        raise HTTPException(status_code=400, detail="Invalid column id list")
    await _require_write(session, user_id, board_id)
    r0 = await session.execute(
        select(TodoColumnModel).where(TodoColumnModel.id == body.ordered_column_ids[0])
    )
    col0 = r0.scalar_one_or_none()
    if not col0 or col0.board_id != board_id:
        raise HTTPException(status_code=400, detail="Invalid column id list")
    repo = KanbanRepository(session)
    ok = await repo.reorder_columns(user_id, body.ordered_column_ids)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Invalid column id list (must match all columns on the board)",
        )
    await session.commit()
    return await build_board_out(session, board_id)


async def _ensure_card_on_board(
    repo: KanbanRepository,
    user_id: int,
    board_id: int,
    card_id: int,
    *,
    need_write: bool,
) -> None:
    card = await repo.get_card_if_owned(user_id, card_id, need_write=need_write)
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    col = await repo.get_column_if_owned(user_id, card.column_id, need_write=False)
    if not col or col.board_id != board_id:
        raise HTTPException(status_code=404, detail="Card not found")


@boards_router.post("/{board_id}/columns/{column_id}/cards", response_model=BoardOut)
async def create_card_nested(
    board_id: int,
    column_id: int,
    body: CreateCardBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    await _require_write(session, user_id, board_id)
    repo = KanbanRepository(session)
    col = await repo.get_column_if_owned(user_id, column_id, need_write=True)
    if not col or col.board_id != board_id:
        raise HTTPException(status_code=404, detail="Column not found")
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
    return await build_board_out(session, board_id)


@boards_router.post("/{board_id}/cards/{card_id}/checklist/items", response_model=BoardOut)
async def create_checklist_item_nested(
    board_id: int,
    card_id: int,
    body: CreateChecklistItemBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    await _require_write(session, user_id, board_id)
    repo = KanbanRepository(session)
    await _ensure_card_on_board(repo, user_id, board_id, card_id, need_write=True)
    row = await repo.add_checklist_item(
        user_id,
        card_id,
        title=body.title,
        insert_at=body.insert_at,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Card not found")
    await session.commit()
    return await build_board_out(session, board_id)


@boards_router.patch("/{board_id}/cards/{card_id}/checklist/items/{item_id}", response_model=BoardOut)
async def patch_checklist_item_nested(
    board_id: int,
    card_id: int,
    item_id: int,
    body: PatchChecklistItemBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    await _require_write(session, user_id, board_id)
    patch = body.model_dump(exclude_unset=True, by_alias=False)
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")
    repo = KanbanRepository(session)
    await _ensure_card_on_board(repo, user_id, board_id, card_id, need_write=True)
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
    return await build_board_out(session, board_id)


@boards_router.delete("/{board_id}/cards/{card_id}/checklist/items/{item_id}", response_model=BoardOut)
async def delete_checklist_item_nested(
    board_id: int,
    card_id: int,
    item_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    await _require_write(session, user_id, board_id)
    repo = KanbanRepository(session)
    await _ensure_card_on_board(repo, user_id, board_id, card_id, need_write=True)
    ok = await repo.delete_checklist_item(user_id, card_id, item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Checklist item not found")
    await session.commit()
    return await build_board_out(session, board_id)


@boards_router.put("/{board_id}/cards/{card_id}/checklist/reorder", response_model=BoardOut)
async def reorder_checklist_nested(
    board_id: int,
    card_id: int,
    body: ReorderChecklistBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    await _require_write(session, user_id, board_id)
    repo = KanbanRepository(session)
    await _ensure_card_on_board(repo, user_id, board_id, card_id, need_write=True)
    ok = await repo.reorder_checklist_items(user_id, card_id, body.ordered_item_ids)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Invalid checklist item id list",
        )
    await session.commit()
    return await build_board_out(session, board_id)


@boards_router.post("/{board_id}/cards/{card_id}/attachments", response_model=BoardOut)
async def upload_card_attachment_nested(
    board_id: int,
    card_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
    file: UploadFile = File(..., description="Тело multipart, поле формы «file»"),
):
    await _require_write(session, user_id, board_id)
    content = await file.read()
    repo = KanbanRepository(session)
    await _ensure_card_on_board(repo, user_id, board_id, card_id, need_write=True)
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
    return await build_board_out(session, board_id)


@boards_router.delete("/{board_id}/cards/{card_id}/attachments/{attachment_id}", response_model=BoardOut)
async def delete_card_attachment_nested(
    board_id: int,
    card_id: int,
    attachment_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    await _require_write(session, user_id, board_id)
    repo = KanbanRepository(session)
    await _ensure_card_on_board(repo, user_id, board_id, card_id, need_write=True)
    ok = await repo.delete_card_attachment(user_id, card_id, attachment_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Attachment not found")
    await session.commit()
    return await build_board_out(session, board_id)


@boards_router.post("/{board_id}/cards/{card_id}/comments", response_model=BoardOut)
async def create_card_comment_nested(
    board_id: int,
    card_id: int,
    body: CreateCommentBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    await _require_write(session, user_id, board_id)
    repo = KanbanRepository(session)
    await _ensure_card_on_board(repo, user_id, board_id, card_id, need_write=True)
    row = await repo.add_card_comment(user_id, card_id, body=body.body)
    if not row:
        raise HTTPException(status_code=404, detail="Card not found")
    await session.commit()
    return await build_board_out(session, board_id)


@boards_router.patch("/{board_id}/cards/{card_id}", response_model=BoardOut)
async def patch_card_nested(
    board_id: int,
    card_id: int,
    body: PatchCardBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    await _require_write(session, user_id, board_id)
    patch = body.model_dump(exclude_unset=True, by_alias=False)
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")
    repo = KanbanRepository(session)
    await _ensure_card_on_board(repo, user_id, board_id, card_id, need_write=True)
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
    if patch.get("column_id") is not None:
        tcol = await repo.get_column_if_owned(user_id, patch["column_id"], need_write=True)
        if not tcol or tcol.board_id != board_id:
            raise HTTPException(status_code=400, detail="Invalid column_id")
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
    if not col or col.board_id != board_id:
        raise HTTPException(status_code=404, detail="Card not found")
    await session.commit()
    return await build_board_out(session, board_id)


@boards_router.delete("/{board_id}/cards/{card_id}", response_model=BoardOut)
async def delete_card_nested(
    board_id: int,
    card_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    await _require_write(session, user_id, board_id)
    repo = KanbanRepository(session)
    await _ensure_card_on_board(repo, user_id, board_id, card_id, need_write=True)
    ok = await repo.delete_card(user_id, card_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Card not found")
    await session.commit()
    return await build_board_out(session, board_id)


@boards_router.put("/{board_id}/columns/{column_id}/cards/reorder", response_model=BoardOut)
async def reorder_cards_nested(
    board_id: int,
    column_id: int,
    body: ReorderCardsBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    await _require_write(session, user_id, board_id)
    repo = KanbanRepository(session)
    col = await repo.get_column_if_owned(user_id, column_id, need_write=True)
    if not col or col.board_id != board_id:
        raise HTTPException(status_code=404, detail="Column not found")
    ok = await repo.reorder_cards_in_column(user_id, column_id, body.ordered_card_ids)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Invalid card id list (must match all cards in the column)",
        )
    await session.commit()
    return await build_board_out(session, board_id)
