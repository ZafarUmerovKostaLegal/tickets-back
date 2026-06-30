from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from application.access_control import ensure_can_list_teams, ensure_can_manage_teams
from application.auth_user_directory import fetch_auth_user_partner_hints_by_id
from application.project_partner_requirement import user_satisfies_partner_rule
from application.team_validation import dedupe_member_ids
from infrastructure.database import get_session
from infrastructure.models import TimeTrackingUserModel
from infrastructure.repositories import TeamRepository, TimeTrackingUserRepository
from presentation.deps import require_bearer_user
from presentation.schemas import (
    TimeTrackingTeamCreateBody,
    TimeTrackingTeamMemberPreviewOut,
    TimeTrackingTeamOut,
    TimeTrackingTeamPatchBody,
)

router = APIRouter(prefix="/teams", tags=["teams"])


async def _validate_partner_user(
    session: AsyncSession,
    authorization: str | None,
    partner_auth_user_id: int,
) -> TimeTrackingUserModel:
    ur = TimeTrackingUserRepository(session)
    row = await ur.get_by_auth_user_id(partner_auth_user_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Партнёр не найден в справочнике учёта времени",
        )
    if row.is_blocked:
        raise HTTPException(status_code=400, detail="Партнёр заблокирован")
    hints = await fetch_auth_user_partner_hints_by_id(authorization or "")
    h = hints.get(partner_auth_user_id) or {}
    if not user_satisfies_partner_rule(row.position, h.get("position"), h.get("role")):
        raise HTTPException(
            status_code=400,
            detail="Указанный пользователь не является партнёром",
        )
    return row


async def _validate_member_ids(
    session: AsyncSession,
    member_auth_user_ids: list[int],
) -> list[int]:
    unique = dedupe_member_ids(member_auth_user_ids)
    if not unique:
        return []
    ur = TimeTrackingUserRepository(session)
    rows = await ur.list_by_auth_user_ids(unique)
    by_id = {r.auth_user_id: r for r in rows}
    for uid in unique:
        row = by_id.get(uid)
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"Пользователь {uid} не найден в справочнике учёта времени",
            )
        if row.is_blocked:
            raise HTTPException(
                status_code=400,
                detail=f"Пользователь {uid} заблокирован",
            )
    return unique


async def _team_out(
    session: AsyncSession,
    team,
    *,
    member_auth_user_ids: list[int] | None = None,
) -> TimeTrackingTeamOut:
    repo = TeamRepository(session)
    member_ids = (
        member_auth_user_ids
        if member_auth_user_ids is not None
        else await repo.list_member_auth_user_ids(team.id)
    )
    lookup_ids = list(dict.fromkeys([int(team.partner_auth_user_id), *member_ids]))
    ur = TimeTrackingUserRepository(session)
    users = await ur.list_by_auth_user_ids(lookup_ids)
    by_id = {u.auth_user_id: u for u in users}
    partner = by_id.get(int(team.partner_auth_user_id))
    partner_display_name = None
    if partner is not None:
        partner_display_name = (partner.display_name or "").strip() or None
    members: list[TimeTrackingTeamMemberPreviewOut] = []
    for uid in member_ids:
        u = by_id.get(uid)
        if u is None:
            continue
        members.append(
            TimeTrackingTeamMemberPreviewOut(
                auth_user_id=uid,
                display_name=(u.display_name or "").strip() or None,
                email=u.email,
            )
        )
    return TimeTrackingTeamOut(
        id=team.id,
        name=team.name,
        partner_auth_user_id=int(team.partner_auth_user_id),
        partner_display_name=partner_display_name,
        member_auth_user_ids=member_ids,
        members=members,
        is_archived=bool(team.is_archived),
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


@router.get("", response_model=list[TimeTrackingTeamOut])
async def list_teams(
    include_archived: bool = Query(False, alias="includeArchived"),
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> list[TimeTrackingTeamOut]:
    ensure_can_list_teams(viewer)
    repo = TeamRepository(session)
    rows = await repo.list_all(include_archived=include_archived)
    members_map = await repo.list_members_by_team_ids([r.id for r in rows])
    out: list[TimeTrackingTeamOut] = []
    for row in rows:
        out.append(
            await _team_out(
                session,
                row,
                member_auth_user_ids=members_map.get(row.id, []),
            )
        )
    return out


@router.post("", response_model=TimeTrackingTeamOut, status_code=201)
async def create_team(
    body: TimeTrackingTeamCreateBody,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
) -> TimeTrackingTeamOut:
    ensure_can_manage_teams(viewer)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Укажите название команды")
    await _validate_partner_user(session, authorization, body.partner_auth_user_id)
    member_ids = await _validate_member_ids(session, body.member_auth_user_ids or [])
    repo = TeamRepository(session)
    if await repo.has_active_name_conflict(name):
        raise HTTPException(
            status_code=409,
            detail="Команда с таким названием уже существует",
        )
    try:
        row = await repo.create(
            name=name,
            partner_auth_user_id=body.partner_auth_user_id,
            member_auth_user_ids=member_ids,
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Команда с таким названием уже существует",
        ) from None
    await session.refresh(row)
    return await _team_out(session, row, member_auth_user_ids=member_ids)


@router.patch("/{team_id}", response_model=TimeTrackingTeamOut)
async def patch_team(
    team_id: str,
    body: TimeTrackingTeamPatchBody,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
) -> TimeTrackingTeamOut:
    ensure_can_manage_teams(viewer)
    repo = TeamRepository(session)
    row = await repo.get_by_id(team_id)
    if not row:
        raise HTTPException(status_code=404, detail="Команда не найдена")

    patch = body.model_dump(exclude_unset=True, by_alias=False)
    if "name" in patch and patch["name"] is not None:
        name = str(patch["name"]).strip()
        if not name:
            raise HTTPException(status_code=400, detail="Укажите название команды")
        patch["name"] = name
        if await repo.has_active_name_conflict(name, exclude_id=row.id):
            raise HTTPException(
                status_code=409,
                detail="Команда с таким названием уже существует",
            )
    if "partner_auth_user_id" in patch and patch["partner_auth_user_id"] is not None:
        await _validate_partner_user(session, authorization, int(patch["partner_auth_user_id"]))
    if "member_auth_user_ids" in patch and patch["member_auth_user_ids"] is not None:
        patch["member_auth_user_ids"] = await _validate_member_ids(
            session,
            list(patch["member_auth_user_ids"]),
        )

    try:
        updated = await repo.update(row.id, patch)
        if not updated:
            raise HTTPException(status_code=404, detail="Команда не найдена")
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Команда с таким названием уже существует",
        ) from None
    await session.refresh(updated)
    return await _team_out(session, updated)


@router.delete("/{team_id}", status_code=204)
async def delete_team(
    team_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> Response:
    ensure_can_manage_teams(viewer)
    repo = TeamRepository(session)
    row = await repo.get_by_id(team_id)
    if not row:
        raise HTTPException(status_code=404, detail="Команда не найдена")
    await repo.delete(team_id)
    await session.commit()
    return Response(status_code=204)
