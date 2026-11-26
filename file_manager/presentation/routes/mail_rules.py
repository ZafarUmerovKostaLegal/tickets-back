"""Mail rules and per-user auto-filing switch (Hub «Правила почты», Outlook add-in)."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.mail_rules import match_problem, normalize_match, normalize_subject
from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.models import FolderModel, MailboxModel, MailRuleModel
from presentation.common import crumbs, ensure_provisioned, folder_in_matter, iso, now, require_matter
from presentation.deps import Viewer, get_viewer

router = APIRouter(tags=["file-manager"])

SCOPES = frozenset({"personal", "shared"})


class RuleIn(BaseModel):
    match: str = Field(default="", max_length=320)
    subject: str = Field(default="", max_length=600)
    matterId: str = Field(min_length=1, max_length=80)
    folderId: str | None = Field(default=None, max_length=36)
    scope: str = Field(default="personal", max_length=10)
    enabled: bool = True


class RulePatch(BaseModel):
    enabled: bool | None = None
    match: str | None = Field(default=None, max_length=320)
    subject: str | None = Field(default=None, max_length=600)
    matterId: str | None = Field(default=None, max_length=80)
    folderId: str | None = Field(default=None, max_length=36)
    clearFolder: bool = False


class AutofileIn(BaseModel):
    enabled: bool


def _rule_out(rule: MailRuleModel, viewer: Viewer) -> dict:
    return {
        "id": rule.id,
        "scope": rule.scope,
        "match": rule.match,
        "subject": rule.subject or "",
        "matterId": rule.matter_id,
        "matterName": rule.matter_name,
        "matterCode": rule.matter_code,
        "clientId": rule.client_id,
        "clientName": rule.client_name,
        "folderId": rule.folder_id,
        "folderPath": rule.folder_path,
        "enabled": rule.enabled,
        "hits": rule.hits,
        "lastHitAt": iso(rule.last_hit_at),
        "ownerName": rule.owner_name,
        "mine": rule.owner_user_id == viewer.user_id,
        "canEdit": _can_edit(rule, viewer),
        "createdAt": iso(rule.created_at),
    }


def _mailbox_out(box: MailboxModel | None) -> dict | None:
    if box is None:
        return None
    return {
        "email": box.email,
        "enabled": box.enabled,
        "since": iso(box.since),
        "filedTotal": box.filed_total,
        "lastRunAt": iso(box.last_run_at),
        "lastFiledAt": iso(box.last_filed_at),
        "lastError": box.last_error or None,
    }


def _can_edit(rule: MailRuleModel, viewer: Viewer) -> bool:
    if rule.scope == "shared":
        return viewer.manager
    return rule.owner_user_id == viewer.user_id


async def _target(session: AsyncSession, viewer: Viewer, matter_id: str, folder_id: str | None) -> dict:
    """Checks the viewer may file into the matter (edit) and resolves names for the rule."""
    dirx, matter, _ = await require_matter(session, viewer, matter_id, "edit")
    await ensure_provisioned(session, viewer, matter)
    folder = await folder_in_matter(session, matter.id, folder_id)
    path = ""
    if folder is not None:
        folders = list(await session.scalars(select(FolderModel).where(FolderModel.matter_id == matter.id)))
        path = " / ".join(crumb["name"] for crumb in crumbs(folders, folder.id))
    client = dirx.clients.get(matter.client_id)
    return {
        "matter_id": matter.id,
        "matter_name": matter.name[:300],
        "matter_code": (matter.code or "")[:64],
        "client_id": matter.client_id,
        "client_name": (client.name if client else "")[:300],
        "folder_id": folder.id if folder else None,
        "folder_path": path[:1000],
    }


def _checked_conditions(raw_match: str, raw_subject: str) -> tuple[str, str]:
    """Sender (address / domain) and subject phrases; at least one of them is required."""
    subject = normalize_subject(raw_subject)
    if not normalize_match(raw_match):
        if not subject:
            raise HTTPException(status_code=400, detail="Укажите отправителя или слова из темы письма")
        return "", subject
    problem = match_problem(raw_match)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    return normalize_match(raw_match), subject


@router.get("/mail/rules")
async def list_rules(session: AsyncSession = Depends(get_session), viewer: Viewer = Depends(get_viewer)):
    rows = list(await session.scalars(
        select(MailRuleModel)
        .where(or_(MailRuleModel.scope == "shared", MailRuleModel.owner_user_id == viewer.user_id))
        .order_by(MailRuleModel.scope.desc(), MailRuleModel.match, MailRuleModel.subject)
    ))
    settings = get_settings()
    return {
        "rules": [_rule_out(rule, viewer) for rule in rows],
        "mailbox": _mailbox_out(await session.get(MailboxModel, viewer.user_id)),
        "canShare": viewer.manager,
        "serverReady": settings.mail_autofile_enabled and settings.graph_ready(),
    }


@router.post("/mail/rules", status_code=201)
async def create_rule(body: RuleIn, session: AsyncSession = Depends(get_session), viewer: Viewer = Depends(get_viewer)):
    scope = body.scope if body.scope in SCOPES else "personal"
    if scope == "shared" and not viewer.manager:
        raise HTTPException(status_code=403, detail="Общие правила создают партнёры и администраторы")
    match, subject = _checked_conditions(body.match, body.subject)
    target = await _target(session, viewer, body.matterId, body.folderId)
    # One rule per sender + subject: saving the same conditions again moves the rule to the new target;
    # other subjects of the same sender are separate rules (each with its own folder).
    query = select(MailRuleModel).where(
        MailRuleModel.scope == scope, MailRuleModel.match == match, MailRuleModel.subject == subject,
    )
    if scope == "personal":
        query = query.where(MailRuleModel.owner_user_id == viewer.user_id)
    rule = await session.scalar(query.limit(1))
    stamp = now()
    if rule is None:
        rule = MailRuleModel(id=str(uuid4()), scope=scope, owner_user_id=viewer.user_id, match=match, subject=subject, created_at=stamp)
        session.add(rule)
    for key, value in target.items():
        setattr(rule, key, value)
    rule.owner_name = viewer.name[:200]
    rule.enabled = body.enabled
    rule.updated_at = stamp
    await session.commit()
    return {"rule": _rule_out(rule, viewer)}


@router.patch("/mail/rules/{rule_id}")
async def update_rule(rule_id: str, body: RulePatch, session: AsyncSession = Depends(get_session), viewer: Viewer = Depends(get_viewer)):
    rule = await session.get(MailRuleModel, rule_id)
    if rule is None or (rule.scope == "personal" and rule.owner_user_id != viewer.user_id):
        raise HTTPException(status_code=404, detail="Правило не найдено")
    if not _can_edit(rule, viewer):
        raise HTTPException(status_code=403, detail="Общие правила меняют партнёры и администраторы")
    if body.match is not None or body.subject is not None:
        rule.match, rule.subject = _checked_conditions(
            rule.match if body.match is None else body.match,
            rule.subject if body.subject is None else body.subject,
        )
    if body.matterId is not None or body.folderId is not None or body.clearFolder:
        folder_id = None if body.clearFolder else (body.folderId if body.folderId is not None else rule.folder_id)
        target = await _target(session, viewer, body.matterId or rule.matter_id, folder_id)
        for key, value in target.items():
            setattr(rule, key, value)
    if body.enabled is not None:
        rule.enabled = body.enabled
    rule.updated_at = now()
    await session.commit()
    return {"rule": _rule_out(rule, viewer)}


@router.delete("/mail/rules/{rule_id}")
async def delete_rule(rule_id: str, session: AsyncSession = Depends(get_session), viewer: Viewer = Depends(get_viewer)):
    rule = await session.get(MailRuleModel, rule_id)
    if rule is None or (rule.scope == "personal" and rule.owner_user_id != viewer.user_id):
        raise HTTPException(status_code=404, detail="Правило не найдено")
    if not _can_edit(rule, viewer):
        raise HTTPException(status_code=403, detail="Общие правила удаляют партнёры и администраторы")
    await session.delete(rule)
    await session.commit()
    return {"ok": True}


@router.put("/mail/autofile")
async def set_autofile(body: AutofileIn, session: AsyncSession = Depends(get_session), viewer: Viewer = Depends(get_viewer)):
    """Turn server-side filing of *my* mailbox on or off. Only mail arriving after «on» is read."""
    box = await session.get(MailboxModel, viewer.user_id)
    stamp = now()
    if body.enabled:
        if not viewer.email or "@" not in viewer.email:
            raise HTTPException(status_code=400, detail="У учётной записи нет почты Microsoft 365")
        if box is None:
            box = MailboxModel(user_id=viewer.user_id, email=viewer.email, since=stamp, created_at=stamp)
            session.add(box)
        elif not box.enabled:
            box.since = stamp
            box.inbox_delta = ""
            box.sent_delta = ""
        box.email = viewer.email
        box.user_name = viewer.name[:200]
        box.enabled = True
        box.last_error = ""
    elif box is not None:
        box.enabled = False
    await session.commit()
    return {"mailbox": _mailbox_out(box)}


@router.post("/mail/autofile/run")
async def run_autofile_now(viewer: Viewer = Depends(get_viewer)):
    """Managers: start a pass right away instead of waiting for the next one."""
    if not viewer.manager:
        raise HTTPException(status_code=403, detail="Доступно партнёрам и администраторам")
    from presentation.autofile import run_once

    asyncio.create_task(run_once())
    return {"started": True}
