from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.access import ACL_LEVELS, REQUEST_LEVELS, has_level, normalize_template
from infrastructure.database import get_session
from infrastructure.models import (
    AccessRequestModel,
    AclModel,
    AuditModel,
    DocumentModel,
    SettingModel,
    VersionModel,
)
from infrastructure.tt_directory import invalidate_access, project_assignees
from presentation.common import (
    TEMPLATE_KEY,
    audit,
    directory,
    iso,
    matter_level,
    now,
    require_manager,
    require_matter,
    template,
)
from presentation.deps import Viewer, get_viewer
from presentation.routes.documents import _audit_out

router = APIRouter(tags=["file-manager"])


class AclIn(BaseModel):
    userId: int = Field(ge=1)
    userName: str = Field(default="", max_length=200)
    level: str = Field(max_length=16)
    note: str = Field(default="", max_length=500)


class AccessRequestIn(BaseModel):
    matterId: str = Field(min_length=1, max_length=80)
    level: str = Field(default="read", max_length=16)
    reason: str = Field(default="", max_length=2000)


class DecisionIn(BaseModel):
    approve: bool
    level: str | None = Field(default=None, max_length=16)


class TemplateIn(BaseModel):
    folders: list[str] = Field(default_factory=list, max_length=100)


def _acl_out(row: AclModel) -> dict:
    return {
        "userId": row.user_id,
        "userName": row.user_name,
        "level": row.level,
        "note": row.note,
        "grantedBy": row.granted_by_name,
        "grantedAt": iso(row.granted_at),
    }


def _request_out(row: AccessRequestModel) -> dict:
    return {
        "id": row.id,
        "matterId": row.matter_id,
        "matterName": row.matter_name,
        "clientName": row.client_name,
        "userId": row.user_id,
        "userName": row.user_name,
        "level": row.level,
        "reason": row.reason,
        "status": row.status,
        "decidedBy": row.decided_by_name,
        "decidedAt": iso(row.decided_at),
        "createdAt": iso(row.created_at),
    }


@router.get("/matters/{matter_id}/security")
async def matter_security(
    matter_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    dirx, matter, _ = await require_matter(session, viewer, matter_id, "manage")
    inherited = await project_assignees(viewer.authorization, matter.id)
    acl = list(await session.scalars(select(AclModel).where(AclModel.matter_id == matter.id)))
    acl_by_user = {row.user_id: row for row in acl}
    effective: dict[int, dict] = {}
    for member in inherited:
        row = acl_by_user.get(member["userId"])
        effective[member["userId"]] = {
            **member,
            "source": "both" if row else "timeTracking",
            "level": row.level if row else ("read" if dirx.matters[matter.id].archived else "edit"),
        }
    for row in acl:
        if row.user_id in effective:
            continue
        effective[row.user_id] = {
            "userId": row.user_id,
            "name": row.user_name or f"#{row.user_id}",
            "position": "",
            "source": "acl",
            "level": row.level,
        }
    client = dirx.clients.get(matter.client_id)
    return {
        "matter": {"id": matter.id, "name": matter.name, "code": matter.code,
                   "clientName": client.name if client else ""},
        "inherited": inherited,
        "acl": [_acl_out(row) for row in acl],
        "effective": sorted(effective.values(), key=lambda item: item["name"].lower()),
    }


@router.put("/matters/{matter_id}/security")
async def set_matter_acl(
    matter_id: str,
    body: AclIn,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    _, matter, _ = await require_matter(session, viewer, matter_id, "manage")
    if body.level not in ACL_LEVELS:
        raise HTTPException(status_code=400, detail="Неизвестный уровень доступа")
    if body.level == "manage" and not viewer.manager:
        raise HTTPException(status_code=403, detail="Права управления выдают только администраторы и партнёры")
    row = await session.get(AclModel, (matter.id, body.userId))
    if row is None:
        row = AclModel(matter_id=matter.id, user_id=body.userId, level=body.level, granted_at=now())
        session.add(row)
    row.level = body.level
    row.user_name = body.userName.strip()[:200] or row.user_name
    row.note = body.note.strip()[:500]
    row.granted_by_name = viewer.name
    row.granted_at = now()
    audit(session, viewer, "acl_set", matter=matter, kind="user", target_id=str(body.userId),
          target_name=row.user_name, detail=body.level)
    await session.commit()
    invalidate_access(body.userId)
    return {"acl": _acl_out(row)}


@router.delete("/matters/{matter_id}/security/{user_id}")
async def remove_matter_acl(
    matter_id: str,
    user_id: int,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    _, matter, _ = await require_matter(session, viewer, matter_id, "manage")
    row = await session.get(AclModel, (matter.id, user_id))
    if row is not None:
        audit(session, viewer, "acl_remove", matter=matter, kind="user", target_id=str(user_id),
              target_name=row.user_name, detail=row.level)
        await session.delete(row)
        await session.commit()
    return {"ok": True}


@router.get("/matrix")
async def access_matrix(
    clientId: str = Query(..., min_length=1),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    """Users × matters of one client: effective level per cell."""
    await require_manager(viewer)
    dirx = await directory(viewer)
    matters = sorted(
        (m for m in dirx.matters.values() if m.client_id == clientId),
        key=lambda m: (m.archived, m.name.lower()),
    )[:40]
    assignees = await asyncio.gather(*(project_assignees(viewer.authorization, m.id) for m in matters))
    acl = list(await session.scalars(select(AclModel).where(AclModel.matter_id.in_([m.id for m in matters] or [""]))))
    users: dict[int, dict] = {}
    cells: dict[str, str] = {}
    for matter, members in zip(matters, assignees):
        for member in members:
            users.setdefault(member["userId"], {"userId": member["userId"], "name": member["name"],
                                                "position": member["position"]})
            cells[f"{member['userId']}:{matter.id}"] = "read" if matter.archived else "edit"
    for row in acl:
        users.setdefault(row.user_id, {"userId": row.user_id, "name": row.user_name or f"#{row.user_id}",
                                       "position": ""})
        cells[f"{row.user_id}:{row.matter_id}"] = row.level
    client = dirx.clients.get(clientId)
    return {
        "client": {"id": clientId, "name": client.name if client else ""},
        "matters": [{"id": m.id, "name": m.name, "code": m.code, "closed": m.archived} for m in matters],
        "users": sorted(users.values(), key=lambda u: u["name"].lower()),
        "cells": cells,
        "explicit": [f"{row.user_id}:{row.matter_id}" for row in acl],
    }


@router.post("/access-requests", status_code=201)
async def create_access_request(
    body: AccessRequestIn,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    if body.level not in REQUEST_LEVELS:
        raise HTTPException(status_code=400, detail="Можно запросить только просмотр или редактирование")
    dirx = await directory(viewer)
    matter = dirx.matters.get(body.matterId)
    acl = await session.get(AclModel, (body.matterId, viewer.user_id))
    if matter is None or (acl is not None and acl.level == "deny"):
        raise HTTPException(status_code=404, detail="Matter не найден")
    level = await matter_level(session, viewer, dirx, matter)
    if has_level(level, body.level):
        raise HTTPException(status_code=400, detail="У вас уже есть такой доступ")
    pending = await session.scalar(select(AccessRequestModel).where(
        AccessRequestModel.matter_id == matter.id,
        AccessRequestModel.user_id == viewer.user_id,
        AccessRequestModel.status == "pending",
    ).limit(1))
    if pending is not None:
        return {"request": _request_out(pending)}
    client = dirx.clients.get(matter.client_id)
    row = AccessRequestModel(
        id=str(uuid4()),
        matter_id=matter.id,
        matter_name=matter.name,
        client_name=client.name if client else "",
        user_id=viewer.user_id,
        user_name=viewer.name,
        level=body.level,
        reason=body.reason.strip()[:2000],
        status="pending",
        created_at=now(),
    )
    session.add(row)
    audit(session, viewer, "request_create", matter=matter, kind="request", target_id=row.id,
          target_name=matter.name, detail=body.level)
    await session.commit()
    return {"request": _request_out(row)}


@router.get("/access-requests")
async def list_access_requests(
    status: str = Query(default="pending"),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    query = select(AccessRequestModel)
    if status != "all":
        query = query.where(AccessRequestModel.status == status)
    if not viewer.manager:
        query = query.where(AccessRequestModel.user_id == viewer.user_id)
    rows = list(await session.scalars(query.order_by(AccessRequestModel.created_at.desc()).limit(300)))
    return {"requests": [_request_out(row) for row in rows]}


@router.post("/access-requests/{request_id}/decision")
async def decide_access_request(
    request_id: str,
    body: DecisionIn,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    await require_manager(viewer)
    row = await session.get(AccessRequestModel, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Запрос не найден")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="Запрос уже рассмотрен")
    dirx = await directory(viewer)
    matter = dirx.matters.get(row.matter_id)
    level = body.level or row.level
    if body.approve:
        if level not in REQUEST_LEVELS | {"manage"}:
            raise HTTPException(status_code=400, detail="Неизвестный уровень доступа")
        acl = await session.get(AclModel, (row.matter_id, row.user_id))
        if acl is None:
            acl = AclModel(matter_id=row.matter_id, user_id=row.user_id, level=level, granted_at=now())
            session.add(acl)
        acl.level = level
        acl.user_name = row.user_name
        acl.note = f"Запрос доступа: {row.reason}"[:500]
        acl.granted_by_name = viewer.name
        acl.granted_at = now()
        row.level = level
    row.status = "approved" if body.approve else "rejected"
    row.decided_by_name = viewer.name
    row.decided_at = now()
    audit(session, viewer, "request_approve" if body.approve else "request_reject", matter=matter,
          matter_id=row.matter_id, kind="user", target_id=str(row.user_id), target_name=row.user_name, detail=level)
    await session.commit()
    invalidate_access(row.user_id)
    return {"request": _request_out(row)}


@router.get("/audit")
async def audit_log(
    matterId: str = Query(default=""),
    userId: int | None = Query(default=None),
    action: str = Query(default=""),
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    await require_manager(viewer)
    query = select(AuditModel)
    count_query = select(func.count(AuditModel.id))
    filters = []
    if matterId:
        filters.append(AuditModel.matter_id == matterId)
    if userId is not None:
        filters.append(AuditModel.user_id == userId)
    if action:
        filters.append(AuditModel.action.in_([part for part in action.split(",") if part]))
    if q.strip():
        like = f"%{q.strip().lower()}%"
        filters.append(or_(
            func.lower(AuditModel.target_name).like(like),
            func.lower(AuditModel.user_name).like(like),
            func.lower(AuditModel.matter_name).like(like),
        ))
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    total = int(await session.scalar(count_query) or 0)
    rows = list(await session.scalars(query.order_by(AuditModel.at.desc()).offset(offset).limit(limit)))
    return {"items": [_audit_out(row) for row in rows], "total": total}


@router.get("/stats")
async def monitoring_stats(
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    await require_manager(viewer)
    stamp = now()
    day = stamp - timedelta(days=1)
    week = stamp - timedelta(days=7)
    live = DocumentModel.deleted_at.is_(None)
    documents = int(await session.scalar(select(func.count(DocumentModel.id)).where(live)) or 0)
    stored_bytes = int(await session.scalar(select(func.coalesce(func.sum(VersionModel.size_bytes), 0))) or 0)
    versions = int(await session.scalar(select(func.count(VersionModel.id))) or 0)
    checked_out = int(await session.scalar(
        select(func.count(DocumentModel.id)).where(live, DocumentModel.checked_out_by_user_id.is_not(None))
    ) or 0)
    in_trash = int(await session.scalar(
        select(func.count(DocumentModel.id)).where(DocumentModel.deleted_at.is_not(None))
    ) or 0)
    pending = int(await session.scalar(
        select(func.count(AccessRequestModel.id)).where(AccessRequestModel.status == "pending")
    ) or 0)

    async def count_action(actions: tuple[str, ...], since) -> int:
        return int(await session.scalar(
            select(func.count(AuditModel.id)).where(AuditModel.action.in_(actions), AuditModel.at >= since)
        ) or 0)

    uploads_week = await count_action(("upload", "version", "checkin"), week)
    downloads_day = await count_action(("download",), day)
    denied_day = await count_action(("denied",), day)
    users = (await session.execute(
        select(AuditModel.user_id, AuditModel.user_name, func.count(AuditModel.id))
        .where(AuditModel.at >= week)
        .group_by(AuditModel.user_id, AuditModel.user_name)
        .order_by(func.count(AuditModel.id).desc())
        .limit(8)
    )).all()
    matters = (await session.execute(
        select(AuditModel.matter_id, AuditModel.matter_name, func.count(AuditModel.id))
        .where(AuditModel.at >= week, AuditModel.matter_id != "")
        .group_by(AuditModel.matter_id, AuditModel.matter_name)
        .order_by(func.count(AuditModel.id).desc())
        .limit(8)
    )).all()
    denied = list(await session.scalars(
        select(AuditModel).where(AuditModel.action == "denied").order_by(AuditModel.at.desc()).limit(10)
    ))
    downloads = (await session.execute(
        select(AuditModel.user_id, AuditModel.user_name, func.count(AuditModel.id))
        .where(AuditModel.action == "download", AuditModel.at >= day)
        .group_by(AuditModel.user_id, AuditModel.user_name)
        .order_by(func.count(AuditModel.id).desc())
        .limit(5)
    )).all()
    return {
        "documents": documents,
        "storedBytes": stored_bytes,
        "versions": versions,
        "checkedOut": checked_out,
        "inTrash": in_trash,
        "pendingRequests": pending,
        "uploadsWeek": uploads_week,
        "downloadsDay": downloads_day,
        "deniedDay": denied_day,
        "activeUsers": [{"userId": uid, "name": name, "events": int(n)} for uid, name, n in users],
        "activeMatters": [{"matterId": mid, "name": name, "events": int(n)} for mid, name, n in matters],
        "topDownloaders": [{"userId": uid, "name": name, "downloads": int(n)} for uid, name, n in downloads],
        "recentDenied": [_audit_out(row) for row in denied],
    }


@router.get("/settings/template")
async def get_template(
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    return {"folders": await template(session), "canEdit": viewer.manager}


@router.put("/settings/template")
async def put_template(
    body: TemplateIn,
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    await require_manager(viewer)
    folders = normalize_template(body.folders)
    row = await session.get(SettingModel, TEMPLATE_KEY)
    if row is None:
        row = SettingModel(key=TEMPLATE_KEY, value={"folders": folders}, updated_at=now())
        session.add(row)
    row.value = {"folders": folders}
    row.updated_at = now()
    audit(session, viewer, "template_update", kind="settings", target_id=TEMPLATE_KEY,
          target_name="Matter template", detail=f"{len(folders)} folders")
    await session.commit()
    return {"folders": folders, "canEdit": True}


@router.delete("/settings/template")
async def reset_template(
    session: AsyncSession = Depends(get_session),
    viewer: Viewer = Depends(get_viewer),
):
    await require_manager(viewer)
    await session.execute(delete(SettingModel).where(SettingModel.key == TEMPLATE_KEY))
    audit(session, viewer, "template_update", kind="settings", target_id=TEMPLATE_KEY,
          target_name="Matter template", detail="reset")
    await session.commit()
    return {"folders": await template(session), "canEdit": True}
