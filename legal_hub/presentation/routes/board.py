from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.board_settings import (
    default_columns,
    default_filter,
    default_folders,
    normalize_columns,
    normalize_filter,
    normalize_folders,
    normalize_scope,
)
from infrastructure.database import get_session
from infrastructure.models import (
    LegalHubBoardColumnsModel,
    LegalHubBoardFilterModel,
    LegalHubProjectFoldersModel,
)
from presentation.deps import require_user

router = APIRouter(tags=["legal-hub-board"])


class ColumnIn(BaseModel):
    id: str = ""
    title: str = ""


class ColumnsIn(BaseModel):
    custom: list[ColumnIn] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list)
    collapsed: list[str] = Field(default_factory=list)


class SidebarTokenIn(BaseModel):
    type: str = ""
    id: str = ""
    name: str = ""


class FolderIn(BaseModel):
    id: str = ""
    name: str = ""
    color: str = "slate"
    projectIds: list[str] = Field(default_factory=list)
    collapsed: bool = False
    clientOrder: list[str] = Field(default_factory=list)


class FoldersIn(BaseModel):
    hideLocked: bool = False
    folders: list[FolderIn] = Field(default_factory=list)
    sidebarOrder: list[SidebarTokenIn] = Field(default_factory=list)
    archivedClients: list[str] = Field(default_factory=list)


class FilterIn(BaseModel):
    scope: str
    onlyLate: bool = False
    onlyUrgent: bool = False
    project: str = ""
    status: str = ""
    stream: str = ""
    owner: str = ""
    priority: str = ""


def _user_id(user: dict) -> int:
    return int(user["id"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _columns_out(row: LegalHubBoardColumnsModel | None) -> dict:
    if row is None:
        return default_columns()
    return normalize_columns({
        "custom": row.custom_columns or [],
        "order": row.column_order or [],
        "collapsed": row.collapsed or [],
    })


def _folders_out(row: LegalHubProjectFoldersModel | None) -> dict:
    if row is None:
        return default_folders()
    return normalize_folders({
        "hideLocked": row.hide_locked,
        "folders": row.folders or [],
        "sidebarOrder": row.sidebar_order or [],
        "archivedClients": row.archived_clients or [],
    })


def _filter_out(row: LegalHubBoardFilterModel) -> dict:
    return {
        "scope": row.scope,
        "onlyLate": row.only_late,
        "onlyUrgent": row.only_urgent,
        "project": row.project,
        "status": row.status,
        "stream": row.stream,
        "owner": row.owner,
        "priority": row.priority,
    }


@router.get("/board")
async def get_board(
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    uid = _user_id(user)
    columns = await session.get(LegalHubBoardColumnsModel, uid)
    folders = await session.get(LegalHubProjectFoldersModel, uid)
    filters = await session.scalars(
        select(LegalHubBoardFilterModel)
        .where(LegalHubBoardFilterModel.user_id == uid)
        .order_by(LegalHubBoardFilterModel.scope)
    )
    return {
        "columns": _columns_out(columns),
        "folders": _folders_out(folders),
        "filters": [_filter_out(row) for row in filters],
    }


@router.put("/board/columns")
async def put_columns(
    body: ColumnsIn,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    saved = normalize_columns(body.model_dump())
    uid = _user_id(user)
    row = await session.get(LegalHubBoardColumnsModel, uid)
    if row is None:
        row = LegalHubBoardColumnsModel(user_id=uid, updated_at=_now())
        session.add(row)
    row.custom_columns = saved["custom"]
    row.column_order = saved["order"]
    row.collapsed = saved["collapsed"]
    row.updated_at = _now()
    await session.commit()
    return saved


@router.put("/board/folders")
async def put_folders(
    body: FoldersIn,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    saved = normalize_folders(body.model_dump())
    uid = _user_id(user)
    row = await session.get(LegalHubProjectFoldersModel, uid)
    if row is None:
        row = LegalHubProjectFoldersModel(user_id=uid, updated_at=_now())
        session.add(row)
    row.hide_locked = saved["hideLocked"]
    row.folders = saved["folders"]
    row.sidebar_order = saved["sidebarOrder"]
    row.archived_clients = saved["archivedClients"]
    row.updated_at = _now()
    await session.commit()
    return saved


@router.put("/board/filters")
async def put_filters(
    body: FilterIn,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    try:
        saved = normalize_filter(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    uid = _user_id(user)
    row = await session.get(LegalHubBoardFilterModel, (uid, saved["scope"]))
    if row is None:
        row = LegalHubBoardFilterModel(user_id=uid, scope=saved["scope"], updated_at=_now())
        session.add(row)
    row.only_late = saved["onlyLate"]
    row.only_urgent = saved["onlyUrgent"]
    row.project = saved["project"]
    row.status = saved["status"]
    row.stream = saved["stream"]
    row.owner = saved["owner"]
    row.priority = saved["priority"]
    row.updated_at = _now()
    await session.commit()
    return saved


def _known_scope(scope: str) -> str:
    try:
        return normalize_scope(scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/board/filters/{scope}")
async def get_filter(
    scope: str,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    chosen = _known_scope(scope)
    row = await session.get(LegalHubBoardFilterModel, (_user_id(user), chosen))
    if row is None:
        return default_filter(chosen)
    return _filter_out(row)
