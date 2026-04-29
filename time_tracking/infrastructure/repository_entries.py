from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, cast, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import DateTime

from application.time_rounding import hours_from_seconds, resolve_duration_for_entry
from infrastructure.models import TimeEntryModel, TimeManagerClientTaskModel
from infrastructure.repository_shared import _now_utc, _to_decimal


class TimeEntryRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    def _aggregate_triplet(self):

        return (
            func.coalesce(
                func.sum(case((TimeEntryModel.is_billable.is_(True), TimeEntryModel.hours), else_=0)),
                0,
            ).label("billable"),
            func.coalesce(
                func.sum(case((TimeEntryModel.is_billable.is_(False), TimeEntryModel.hours), else_=0)),
                0,
            ).label("non_bill"),
            func.coalesce(func.sum(TimeEntryModel.hours), 0).label("total"),
        )

    def _project_entry_conditions(
        self,
        project_id: str,
        date_from: date | None,
        date_to: date | None,
    ) -> list[Any]:
        cond: list[Any] = [
            TimeEntryModel.voided_at.is_(None),
            TimeEntryModel.project_id == project_id,
        ]
        if date_from is not None:
            cond.append(TimeEntryModel.work_date >= date_from)
        if date_to is not None:
            cond.append(TimeEntryModel.work_date <= date_to)
        return cond

    async def aggregate_by_user(
        self,
        date_from: date,
        date_to: date,
    ) -> dict[int, tuple[Decimal, Decimal, Decimal]]:
        q = (
            select(
                TimeEntryModel.auth_user_id,
                *self._aggregate_triplet(),
            )
            .where(
                TimeEntryModel.work_date >= date_from,
                TimeEntryModel.work_date <= date_to,
                TimeEntryModel.voided_at.is_(None),
            )
            .group_by(TimeEntryModel.auth_user_id)
        )
        r = await self._session.execute(q)
        out: dict[int, tuple[Decimal, Decimal, Decimal]] = {}
        for row in r.all():
            out[int(row.auth_user_id)] = (
                _to_decimal(row.total),
                _to_decimal(row.billable),
                _to_decimal(row.non_bill),
            )
        return out

    async def aggregate_totals_for_project(
        self,
        project_id: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[Decimal, Decimal, Decimal]:
        q = select(*self._aggregate_triplet()).where(
            and_(*self._project_entry_conditions(project_id, date_from, date_to))
        )
        row = (await self._session.execute(q)).one()
        return _to_decimal(row.total), _to_decimal(row.billable), _to_decimal(row.non_bill)

    async def aggregate_hours_by_week_for_project(
        self,
        project_id: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[tuple[date, Decimal, Decimal, Decimal]]:
        week_expr = func.date_trunc("week", cast(TimeEntryModel.work_date, DateTime))
        q = (
            select(
                week_expr.label("wk"),
                *self._aggregate_triplet(),
            )
            .where(and_(*self._project_entry_conditions(project_id, date_from, date_to)))
            .group_by(week_expr)
            .order_by(week_expr)
        )
        r = await self._session.execute(q)
        out: list[tuple[date, Decimal, Decimal, Decimal]] = []
        for row in r.all():
            raw_wk = row.wk
            if isinstance(raw_wk, datetime):
                week_date = raw_wk.date()
            elif isinstance(raw_wk, date):
                week_date = raw_wk
            else:
                week_date = date.fromisoformat(str(raw_wk)[:10])
            out.append((week_date, _to_decimal(row.total), _to_decimal(row.billable), _to_decimal(row.non_bill)))
        return out

    async def aggregate_by_user_for_project(
        self,
        date_from: date | None,
        date_to: date | None,
        project_id: str,
    ) -> dict[int, tuple[Decimal, Decimal, Decimal]]:
        q = (
            select(
                TimeEntryModel.auth_user_id,
                *self._aggregate_triplet(),
            )
            .where(and_(*self._project_entry_conditions(project_id, date_from, date_to)))
            .group_by(TimeEntryModel.auth_user_id)
        )
        r = await self._session.execute(q)
        out: dict[int, tuple[Decimal, Decimal, Decimal]] = {}
        for row in r.all():
            out[int(row.auth_user_id)] = (
                _to_decimal(row.total),
                _to_decimal(row.billable),
                _to_decimal(row.non_bill),
            )
        return out

    async def list_auth_users_with_entries_on_project(
        self,
        date_from: date,
        date_to: date,
        project_id: str,
    ) -> list[int]:
        q = (
            select(TimeEntryModel.auth_user_id)
            .distinct()
            .where(
                TimeEntryModel.work_date >= date_from,
                TimeEntryModel.work_date <= date_to,
                TimeEntryModel.project_id == project_id,
                TimeEntryModel.voided_at.is_(None),
            )
        )
        r = await self._session.execute(q)
        return [int(x) for x in r.scalars().all()]

    async def list_for_user(
        self,
        auth_user_id: int,
        date_from: date,
        date_to: date,
    ) -> list[TimeEntryModel]:
        q = (
            select(TimeEntryModel)
            .where(
                TimeEntryModel.auth_user_id == auth_user_id,
                TimeEntryModel.work_date >= date_from,
                TimeEntryModel.work_date <= date_to,
            )

            .order_by(
                TimeEntryModel.work_date.asc(),
                TimeEntryModel.created_at.asc(),
                TimeEntryModel.id.asc(),
            )
        )
        r = await self._session.execute(q)
        return list(r.scalars().all())

    async def get_by_id(self, auth_user_id: int, entry_id: str) -> TimeEntryModel | None:
        r = await self._session.execute(
            select(TimeEntryModel).where(
                TimeEntryModel.auth_user_id == auth_user_id,
                TimeEntryModel.id == entry_id,
            )
        )
        return r.scalars().one_or_none()

    @staticmethod
    def _resolve_duration_seconds(
        duration_seconds: int | None,
        hours: Decimal | None,
    ) -> int:

        return resolve_duration_for_entry(duration_seconds, hours)

    async def create(
        self,
        *,
        entry_id: str,
        auth_user_id: int,
        work_date: date,
        duration_seconds: int | None = None,
        hours: Decimal | None = None,
        is_billable: bool,
        project_id: str | None,
        task_id: str | None = None,
        description: str | None,
        external_reference_url: str | None = None,
    ) -> TimeEntryModel:
        sec = self._resolve_duration_seconds(duration_seconds, hours)
        h = hours_from_seconds(sec)
        row = TimeEntryModel(
            id=entry_id,
            auth_user_id=auth_user_id,
            work_date=work_date,
            duration_seconds=sec,
            hours=h,

            rounded_hours=h,
            is_billable=is_billable,
            project_id=project_id,
            task_id=task_id,
            description=description,
            external_reference_url=(external_reference_url.strip() or None)
            if isinstance(external_reference_url, str)
            else None,
            created_at=_now_utc(),
            updated_at=None,
        )
        self._session.add(row)
        return row

    async def update(
        self,
        *,
        auth_user_id: int,
        entry_id: str,
        patch: dict[str, Any],
    ) -> TimeEntryModel:
        row = await self.get_by_id(auth_user_id, entry_id)
        if not row:
            raise LookupError("not_found")
        if row.voided_at is not None:
            raise ValueError("Запись снята с учёта менеджером и не может быть изменена")
        duration_changed = "duration_seconds" in patch or "hours" in patch
        if duration_changed:
            sec = self._resolve_duration_seconds(
                patch.get("duration_seconds"),
                _to_decimal(patch["hours"]) if "hours" in patch and patch["hours"] is not None else None,
            )
            h = hours_from_seconds(sec)
            row.duration_seconds = sec
            row.hours = h
            row.rounded_hours = h
        if "work_date" in patch:
            row.work_date = patch["work_date"]
        if "is_billable" in patch:
            row.is_billable = bool(patch["is_billable"])
        if "project_id" in patch:
            row.project_id = patch["project_id"]
        if "task_id" in patch:
            row.task_id = patch["task_id"]
        if "description" in patch:
            row.description = patch["description"]
        if "external_reference_url" in patch:
            v = patch["external_reference_url"]
            if v is None or (isinstance(v, str) and not v.strip()):
                row.external_reference_url = None
            elif isinstance(v, str):
                row.external_reference_url = v.strip()[:4000]
        row.updated_at = _now_utc()
        self._session.add(row)
        return row

    async def aggregate_task_hours_for_project(
        self,
        project_id: str,
        date_from: date | None,
        date_to: date | None,
    ) -> list[tuple[str, str, bool, Decimal]]:
        cond = [
            *self._project_entry_conditions(project_id, date_from, date_to),
            TimeEntryModel.task_id.is_not(None),
        ]
        q = (
            select(
                TimeManagerClientTaskModel.id,
                TimeManagerClientTaskModel.name,
                TimeManagerClientTaskModel.billable_by_default,
                func.coalesce(func.sum(TimeEntryModel.hours), 0).label("hrs"),
            )
            .select_from(TimeEntryModel)
            .join(TimeManagerClientTaskModel, TimeManagerClientTaskModel.id == TimeEntryModel.task_id)
            .where(and_(*cond))
            .group_by(
                TimeManagerClientTaskModel.id,
                TimeManagerClientTaskModel.name,
                TimeManagerClientTaskModel.billable_by_default,
            )
            .order_by(
                TimeManagerClientTaskModel.billable_by_default.desc(),
                TimeManagerClientTaskModel.name,
            )
        )
        r = await self._session.execute(q)
        return [
            (str(row.id), str(row.name), bool(row.billable_by_default), _to_decimal(row.hrs))
            for row in r.all()
        ]

    async def aggregate_unassigned_hours_by_billable_for_project(
        self,
        project_id: str,
        date_from: date | None,
        date_to: date | None,
    ) -> list[tuple[bool, Decimal]]:
        cond = [
            *self._project_entry_conditions(project_id, date_from, date_to),
            TimeEntryModel.task_id.is_(None),
        ]
        q = (
            select(
                TimeEntryModel.is_billable,
                func.coalesce(func.sum(TimeEntryModel.hours), 0).label("hrs"),
            )
            .where(and_(*cond))
            .group_by(TimeEntryModel.is_billable)
        )
        r = await self._session.execute(q)
        return [(bool(row.is_billable), _to_decimal(row.hrs)) for row in r.all()]

    async def list_entries_for_project(
        self,
        project_id: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[TimeEntryModel]:

        q = (
            select(TimeEntryModel)
            .where(and_(*self._project_entry_conditions(project_id, date_from, date_to)))
            .order_by(
                TimeEntryModel.work_date.asc(),
                TimeEntryModel.created_at.asc(),
                TimeEntryModel.id.asc(),
            )
        )
        r = await self._session.execute(q)
        return list(r.scalars().all())

    async def delete(self, auth_user_id: int, entry_id: str) -> bool:
        row = await self.get_by_id(auth_user_id, entry_id)
        if not row:
            return False
        if row.voided_at is not None:
            return False
        await self._session.execute(
            delete(TimeEntryModel).where(
                TimeEntryModel.auth_user_id == auth_user_id,
                TimeEntryModel.id == entry_id,
            )
        )
        return True

    async def void_entry(
        self,
        auth_user_id: int,
        entry_id: str,
        *,
        voided_by_auth_user_id: int,
        void_kind: str,
    ) -> TimeEntryModel:

        row = await self.get_by_id(auth_user_id, entry_id)
        if not row:
            raise LookupError("not_found")
        if row.voided_at is not None:
            raise ValueError("Запись уже снята с учёта")
        row.voided_at = _now_utc()
        row.voided_by_auth_user_id = int(voided_by_auth_user_id)
        row.void_kind = str(void_kind)[:32]
        row.updated_at = _now_utc()
        self._session.add(row)
        return row
