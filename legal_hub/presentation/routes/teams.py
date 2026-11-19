"""Teams, temporary access, team statistics and the project map."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.teams import TeamScope, TeamsUnavailable, is_active, load_scope
from infrastructure.database import get_session
from infrastructure.models import LegalHubTaskEventModel, LegalHubTaskModel, LegalHubTempAccessModel
from presentation.deps import require_user

router = APIRouter(tags=["legal-hub"])

MAX_TEMP_DAYS = 366
REVIEW = frozenset({"associate", "partner", "pending_review", "in_review", "reviewing"})
CHANGES = frozenset({"changes", "changes_required"})
WAITING = frozenset({"wait", "waiting_external"})
TODO = frozenset({"todo"})
# Pipeline of a project, left to right.
STAGES = ("todo", "progress", "wait", "review", "changes", "done")


async def scope_or_503(session: AsyncSession, user: dict) -> TeamScope:
    try:
        return await load_scope(session, user)
    except TeamsUnavailable as exc:
        raise HTTPException(status_code=503, detail="Не удалось получить состав команд — попробуйте позже") from exc


def is_done(row: LegalHubTaskModel) -> bool:
    return (row.board_column or "") == "done"


def stage_of(row: LegalHubTaskModel) -> str:
    if is_done(row):
        return "done"
    if row.status in REVIEW:
        return "review"
    if row.status in CHANGES:
        return "changes"
    if row.status in WAITING:
        return "wait"
    if row.status in TODO:
        return "todo"
    return "progress"


def in_scope(row: LegalHubTaskModel, scope: TeamScope) -> bool:
    """Own tasks always; otherwise only tasks of people from the viewer's teams."""
    people = {row.assignee_user_id, row.reviewer_user_id or 0, row.created_by_user_id}
    if scope.user_id in people:
        return True
    return bool(people & scope.mates - {0})


async def scoped_tasks(session: AsyncSession, scope: TeamScope, *, include_archived: bool = False) -> list[LegalHubTaskModel]:
    query = select(LegalHubTaskModel)
    if not include_archived:
        query = query.where(LegalHubTaskModel.archived.is_(False))
    rows = await session.scalars(query.order_by(LegalHubTaskModel.updated_at.desc()))
    return [row for row in rows if in_scope(row, scope)]


def _grant_out(grant: LegalHubTempAccessModel) -> dict:
    return {
        "id": grant.id,
        "userId": grant.user_id,
        "name": grant.user_name,
        "email": grant.user_email,
        "expiresOn": grant.expires_on.isoformat(),
        "grantedBy": grant.granted_by_name,
        "active": is_active(grant, date.today()),
    }


@router.get("/teams")
async def my_teams(session: AsyncSession = Depends(get_session), user: dict = Depends(require_user)):
    scope = await scope_or_503(session, user)
    today = date.today()
    return {
        "userId": scope.user_id,
        "mates": sorted(scope.mates),
        "teams": [
            {
                "id": team.id,
                "name": team.name,
                "partnerId": team.partner_id,
                "memberIds": team.member_ids,
                "members": team.members,
                "viaTemp": team.via_temp,
                "canManage": scope.leads(team.id),
                "temps": [_grant_out(g) for g in team.temps if is_active(g, today)],
            }
            for team in scope.teams
        ],
    }


class TempAccessIn(BaseModel):
    userId: int = Field(gt=0)
    userName: str = Field(default="", max_length=200)
    userEmail: str = Field(default="", max_length=320)
    expiresOn: date


@router.post("/teams/{team_id}/temp-access", status_code=201)
async def grant_temp_access(
    team_id: str,
    body: TempAccessIn,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    scope = await scope_or_503(session, user)
    team = next((t for t in scope.teams if t.id == team_id), None)
    if team is None or not scope.leads(team_id):
        raise HTTPException(status_code=403, detail="Временный доступ выдаёт партнёр команды")
    today = date.today()
    if body.expiresOn < today:
        raise HTTPException(status_code=400, detail="Дата окончания доступа уже прошла")
    if body.expiresOn > today + timedelta(days=MAX_TEMP_DAYS):
        raise HTTPException(status_code=400, detail="Временный доступ — не больше года")
    if body.userId in team.people(today) and not any(g.user_id == body.userId for g in team.temps):
        raise HTTPException(status_code=409, detail="Сотрудник уже в команде")
    for grant in team.temps:
        if grant.user_id == body.userId and is_active(grant, today):
            grant.expires_on = body.expiresOn
            await session.commit()
            return _grant_out(grant)
    grant = LegalHubTempAccessModel(
        id=str(uuid4()),
        team_id=team_id,
        user_id=body.userId,
        user_name=body.userName.strip(),
        user_email=body.userEmail.strip(),
        expires_on=body.expiresOn,
        granted_by_user_id=scope.user_id,
        granted_by_name=str(user.get("display_name") or user.get("email") or ""),
        created_at=datetime.now(timezone.utc),
    )
    session.add(grant)
    await session.commit()
    return _grant_out(grant)


@router.delete("/teams/{team_id}/temp-access/{grant_id}")
async def revoke_temp_access(
    team_id: str,
    grant_id: str,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_user),
):
    scope = await scope_or_503(session, user)
    if not scope.leads(team_id):
        raise HTTPException(status_code=403, detail="Временный доступ отзывает партнёр команды")
    grant = await session.get(LegalHubTempAccessModel, grant_id)
    if grant is None or grant.team_id != team_id:
        raise HTTPException(status_code=404, detail="Доступ не найден")
    grant.revoked = True
    await session.commit()
    return {"ok": True}


def _project_key(row: LegalHubTaskModel) -> tuple[str, str]:
    key = (row.project_id or "").strip() or (row.project_label or "").strip().lower() or "—"
    return key, (row.project_label or "").strip() or "Без проекта"


def project_stage(counts: Counter) -> str:
    """Where the project is: done when nothing is open, otherwise the stage holding most open tasks
    (ties go to the earlier stage — the project is only as far as its slowest part)."""
    open_counts = {stage: counts.get(stage, 0) for stage in STAGES if stage != "done"}
    if not any(open_counts.values()):
        return "done"
    best = max(open_counts.values())
    return next(stage for stage in STAGES if open_counts.get(stage, 0) == best)


def build_project_map(rows: list[LegalHubTaskModel], today: date) -> list[dict]:
    groups: dict[str, dict] = {}
    for row in rows:
        key, label = _project_key(row)
        group = groups.setdefault(key, {
            "id": key, "name": label, "counts": Counter(), "overdue": 0, "nextDue": None,
            "people": set(), "updatedAt": row.updated_at,
        })
        stage = stage_of(row)
        group["counts"][stage] += 1
        group["people"].add(row.assignee_user_id)
        if stage != "done" and row.due_on:
            if row.due_on < today:
                group["overdue"] += 1
            elif group["nextDue"] is None or row.due_on < group["nextDue"]:
                group["nextDue"] = row.due_on
        if row.updated_at > group["updatedAt"]:
            group["updatedAt"] = row.updated_at
    out = []
    for group in groups.values():
        counts: Counter = group["counts"]
        total = sum(counts.values())
        out.append({
            "id": group["id"],
            "name": group["name"],
            "total": total,
            "done": counts.get("done", 0),
            "progress": round(100 * counts.get("done", 0) / total) if total else 0,
            "stage": project_stage(counts),
            "stages": {stage: counts.get(stage, 0) for stage in STAGES},
            "overdue": group["overdue"],
            "nextDue": group["nextDue"].isoformat() if group["nextDue"] else None,
            "people": sorted(group["people"]),
            "updatedAt": group["updatedAt"].isoformat(),
        })
    out.sort(key=lambda p: (p["stage"] == "done", -p["overdue"], p["name"].lower()))
    return out


@router.get("/projects/map")
async def project_map(session: AsyncSession = Depends(get_session), user: dict = Depends(require_user)):
    scope = await scope_or_503(session, user)
    return {"stages": list(STAGES), "projects": build_project_map(await scoped_tasks(session, scope), date.today())}


@router.get("/stats")
async def team_stats(session: AsyncSession = Depends(get_session), user: dict = Depends(require_user)):
    """Team dashboard: totals, per-person load and throughput over the last 30 days."""
    scope = await scope_or_503(session, user)
    rows = await scoped_tasks(session, scope, include_archived=True)
    today = date.today()
    since = datetime.now(timezone.utc) - timedelta(days=30)
    done_events = {
        task_id: at for task_id, at in (await session.execute(
            select(LegalHubTaskEventModel.task_id, LegalHubTaskEventModel.created_at)
            .where(LegalHubTaskEventModel.kind == "approved")
        )).all()
    }

    def aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    people: dict[int, dict] = defaultdict(lambda: {"open": 0, "overdue": 0, "done30": 0, "review": 0, "byStage": Counter()})
    totals = Counter()
    returned30 = 0
    for row in rows:
        stage = stage_of(row)
        person = people[row.assignee_user_id]
        if stage == "done" or row.archived:
            finished = done_events.get(row.id) or row.updated_at
            if aware(finished) >= since:
                person["done30"] += 1
                totals["done30"] += 1
            continue
        person["open"] += 1
        person["byStage"][stage] += 1
        totals["open"] += 1
        totals[stage] += 1
        if row.due_on and row.due_on < today:
            person["overdue"] += 1
            totals["overdue"] += 1
        if row.due_on and today <= row.due_on <= today + timedelta(days=7):
            totals["dueWeek"] += 1
        if row.reviewer_user_id:
            people[row.reviewer_user_id]["review"] += 1 if stage == "review" else 0
    returned30 = (await session.execute(
        select(LegalHubTaskEventModel.id).where(LegalHubTaskEventModel.kind == "returned", LegalHubTaskEventModel.created_at >= since)
    )).all()
    return {
        "totals": {
            "open": totals["open"],
            "overdue": totals["overdue"],
            "dueWeek": totals["dueWeek"],
            "review": totals["review"],
            "changes": totals["changes"],
            "done30": totals["done30"],
            "returned30": len(returned30),
        },
        "people": [
            {"userId": uid, **{k: v for k, v in data.items() if k != "byStage"}, "byStage": dict(data["byStage"])}
            for uid, data in sorted(people.items(), key=lambda item: (-item[1]["open"], item[0]))
            if uid in scope.mates
        ],
        "projects": build_project_map([row for row in rows if not row.archived], today),
    }
