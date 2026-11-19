"""Team scope of Legal Hub: who works with whom.

Teams come from time tracking (partner + members). A partner can add a colleague to the team for a
limited time. Tasks are visible, and can be assigned, strictly inside the viewer's teams — regardless
of the role of the person inside the team.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.config import get_settings
from infrastructure.models import LegalHubTeamRosterModel, LegalHubTempAccessModel

_CACHE_SECONDS = 60.0
_cache: dict[int, tuple[float, list[dict]]] = {}
ADMIN_ROLES = frozenset({"admin", "super_admin", "superadmin"})


class TeamsUnavailable(Exception):
    """Time tracking did not answer: callers must not widen access on failure."""


@dataclass
class Team:
    id: str
    name: str
    partner_id: int
    member_ids: list[int]
    members: list[dict]
    temps: list[LegalHubTempAccessModel] = field(default_factory=list)
    via_temp: bool = False

    def people(self, today: date) -> set[int]:
        ids = {self.partner_id, *self.member_ids}
        ids.update(grant.user_id for grant in self.temps if is_active(grant, today))
        ids.discard(0)
        return ids


@dataclass
class TeamScope:
    user_id: int
    teams: list[Team]
    is_admin: bool = False

    @property
    def mates(self) -> set[int]:
        today = date.today()
        ids: set[int] = {self.user_id}
        for team in self.teams:
            ids |= team.people(today)
        return ids

    def leads(self, team_id: str) -> bool:
        return self.is_admin or any(team.id == team_id and team.partner_id == self.user_id for team in self.teams)


def is_active(grant: LegalHubTempAccessModel, today: date) -> bool:
    return not grant.revoked and grant.expires_on >= today


def _user_id(user: dict) -> int:
    return int(user["id"])


async def _fetch_my_teams(user: dict) -> list[dict]:
    uid = _user_id(user)
    cached = _cache.get(uid)
    if cached and time.monotonic() - cached[0] < _CACHE_SECONDS:
        return cached[1]
    base = (get_settings().time_tracking_service_url or "").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{base}/teams/mine", headers={"Authorization": str(user.get("authorization") or "")})
    except httpx.RequestError as exc:
        raise TeamsUnavailable from exc
    if response.status_code >= 400:
        raise TeamsUnavailable(f"HTTP {response.status_code}")
    rows = response.json()
    rows = rows if isinstance(rows, list) else []
    _cache[uid] = (time.monotonic(), rows)
    return rows


def clear_cache() -> None:
    _cache.clear()


def _member_preview(row: dict) -> list[dict]:
    out = []
    for item in row.get("members") or []:
        if isinstance(item, dict) and item.get("auth_user_id") is not None:
            out.append({
                "id": int(item["auth_user_id"]),
                "name": str(item.get("display_name") or item.get("email") or ""),
                "email": str(item.get("email") or ""),
            })
    return out


async def load_scope(session: AsyncSession, user: dict) -> TeamScope:
    """Teams the user leads or belongs to, plus teams they were temporarily added to."""
    uid = _user_id(user)
    rows = await _fetch_my_teams(user)
    now = datetime.now(timezone.utc)
    teams: dict[str, Team] = {}
    for row in rows:
        team_id = str(row.get("id") or "")
        if not team_id or row.get("is_archived"):
            continue
        member_ids = [int(x) for x in row.get("member_auth_user_ids") or [] if str(x).lstrip("-").isdigit()]
        team = Team(
            id=team_id,
            name=str(row.get("name") or ""),
            partner_id=int(row.get("partner_auth_user_id") or 0),
            member_ids=member_ids,
            members=_member_preview(row),
        )
        if team.partner_id and all(m["id"] != team.partner_id for m in team.members):
            team.members.insert(0, {
                "id": team.partner_id,
                "name": str(row.get("partner_display_name") or ""),
                "email": "",
                "lead": True,
            })
        teams[team_id] = team
        roster = await session.get(LegalHubTeamRosterModel, team_id)
        if roster is None:
            roster = LegalHubTeamRosterModel(team_id=team_id, updated_at=now)
            session.add(roster)
        roster.name = team.name
        roster.partner_user_id = team.partner_id
        roster.member_ids = member_ids
        roster.members = team.members
        roster.updated_at = now

    today = date.today()
    own_grants = [g for g in await session.scalars(
        select(LegalHubTempAccessModel).where(LegalHubTempAccessModel.user_id == uid)
    ) if is_active(g, today)]
    for grant in own_grants:
        if grant.team_id in teams:
            continue
        roster = await session.get(LegalHubTeamRosterModel, grant.team_id)
        if roster is None:
            continue
        teams[grant.team_id] = Team(
            id=roster.team_id,
            name=roster.name,
            partner_id=roster.partner_user_id,
            member_ids=list(roster.member_ids or []),
            members=list(roster.members or []),
            via_temp=True,
        )

    if teams:
        grants = list(await session.scalars(
            select(LegalHubTempAccessModel).where(LegalHubTempAccessModel.team_id.in_(list(teams)))
        ))
        for grant in grants:
            teams[grant.team_id].temps.append(grant)
    await session.commit()
    role = str(user.get("role") or "").strip().lower()
    return TeamScope(user_id=uid, teams=list(teams.values()), is_admin=role in ADMIN_ROLES)
