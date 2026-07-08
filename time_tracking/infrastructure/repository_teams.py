from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import TimeTrackingTeamMemberModel, TimeTrackingTeamModel
from infrastructure.repository_shared import _now_utc


class TeamRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_all(self, *, include_archived: bool = False) -> list[TimeTrackingTeamModel]:
        q = select(TimeTrackingTeamModel).order_by(TimeTrackingTeamModel.name.asc())
        if not include_archived:
            q = q.where(TimeTrackingTeamModel.is_archived.is_(False))
        r = await self._session.execute(q)
        return list(r.scalars().all())

    async def get_by_id(self, team_id: str) -> TimeTrackingTeamModel | None:
        tid = (team_id or "").strip()
        if not tid:
            return None
        r = await self._session.execute(
            select(TimeTrackingTeamModel).where(TimeTrackingTeamModel.id == tid)
        )
        return r.scalars().one_or_none()

    async def list_member_auth_user_ids(self, team_id: str) -> list[int]:
        tid = (team_id or "").strip()
        if not tid:
            return []
        r = await self._session.execute(
            select(TimeTrackingTeamMemberModel.auth_user_id)
            .where(TimeTrackingTeamMemberModel.team_id == tid)
            .order_by(TimeTrackingTeamMemberModel.auth_user_id.asc())
        )
        return [int(x) for x in r.scalars().all()]

    async def list_members_by_team_ids(self, team_ids: list[str]) -> dict[str, list[int]]:
        ids = [(t or "").strip() for t in team_ids if (t or "").strip()]
        if not ids:
            return {}
        r = await self._session.execute(
            select(TimeTrackingTeamMemberModel.team_id, TimeTrackingTeamMemberModel.auth_user_id).where(
                TimeTrackingTeamMemberModel.team_id.in_(ids)
            )
        )
        out: dict[str, list[int]] = {tid: [] for tid in ids}
        for team_id, auth_user_id in r.all():
            out.setdefault(str(team_id), []).append(int(auth_user_id))
        for tid in out:
            out[tid].sort()
        return out

    async def list_member_auth_user_ids_for_partner(self, partner_auth_user_id: int) -> list[int]:
        pid = int(partner_auth_user_id)
        if pid <= 0:
            return []
        r = await self._session.execute(
            select(TimeTrackingTeamMemberModel.auth_user_id)
            .join(
                TimeTrackingTeamModel,
                TimeTrackingTeamMemberModel.team_id == TimeTrackingTeamModel.id,
            )
            .where(
                TimeTrackingTeamModel.partner_auth_user_id == pid,
                TimeTrackingTeamModel.is_archived.is_(False),
            )
            .order_by(TimeTrackingTeamMemberModel.auth_user_id.asc())
        )
        return sorted({int(x) for x in r.scalars().all()})

    async def has_active_name_conflict(self, name: str, *, exclude_id: str | None = None) -> bool:
        n = (name or "").strip()
        if not n:
            return False
        q = select(func.count()).select_from(TimeTrackingTeamModel).where(
            func.lower(func.trim(TimeTrackingTeamModel.name)) == n.casefold(),
            TimeTrackingTeamModel.is_archived.is_(False),
        )
        if exclude_id:
            q = q.where(TimeTrackingTeamModel.id != exclude_id)
        r = await self._session.execute(q)
        return int(r.scalar_one() or 0) > 0

    async def create(
        self,
        *,
        name: str,
        partner_auth_user_id: int,
        member_auth_user_ids: list[int],
        is_archived: bool = False,
    ) -> TimeTrackingTeamModel:
        now = _now_utc()
        row = TimeTrackingTeamModel(
            id=str(uuid.uuid4()),
            name=name.strip(),
            partner_auth_user_id=int(partner_auth_user_id),
            is_archived=bool(is_archived),
            created_at=now,
            updated_at=None,
        )
        self._session.add(row)
        await self._session.flush()
        await self.replace_members(row.id, member_auth_user_ids)
        return row

    async def replace_members(self, team_id: str, member_auth_user_ids: list[int]) -> None:
        tid = (team_id or "").strip()
        await self._session.execute(
            delete(TimeTrackingTeamMemberModel).where(TimeTrackingTeamMemberModel.team_id == tid)
        )
        for uid in member_auth_user_ids:
            self._session.add(
                TimeTrackingTeamMemberModel(team_id=tid, auth_user_id=int(uid))
            )

    async def update(self, team_id: str, patch: dict[str, Any]) -> TimeTrackingTeamModel | None:
        row = await self.get_by_id(team_id)
        if not row:
            return None
        if "name" in patch and patch["name"] is not None:
            row.name = str(patch["name"]).strip()
        if "partner_auth_user_id" in patch and patch["partner_auth_user_id"] is not None:
            row.partner_auth_user_id = int(patch["partner_auth_user_id"])
        if "is_archived" in patch:
            row.is_archived = bool(patch["is_archived"])
        row.updated_at = _now_utc()
        self._session.add(row)
        if "member_auth_user_ids" in patch and patch["member_auth_user_ids"] is not None:
            await self.replace_members(row.id, list(patch["member_auth_user_ids"]))
        return row

    async def delete(self, team_id: str) -> bool:
        row = await self.get_by_id(team_id)
        if not row:
            return False
        await self._session.delete(row)
        return True
