from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.hourly_rate_logic import intervals_overlap, validate_range_order
from infrastructure.models import UserHourlyRateModel
from infrastructure.repository_shared import _now_utc, _to_decimal

_RATE_KINDS = frozenset({"billable", "cost"})


def _rate_currency_key(row: UserHourlyRateModel) -> str:
    return (row.currency or "USD").strip().upper()[:10] or "USD"


def _applies_scope_match(row: UserHourlyRateModel, applies_to_project_id: str | None) -> bool:
    a = getattr(row, "applies_to_project_id", None)
    a = (a.strip() or None) if isinstance(a, str) else (a or None)
    b = (applies_to_project_id or "").strip() or None
    return a == b


class HourlyRateRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_by_user_and_kind(self, auth_user_id: int, rate_kind: str) -> list[UserHourlyRateModel]:
        q = (
            select(UserHourlyRateModel)
            .where(
                UserHourlyRateModel.auth_user_id == auth_user_id,
                UserHourlyRateModel.rate_kind == rate_kind,
            )
            .order_by(UserHourlyRateModel.valid_from.asc().nullsfirst(), UserHourlyRateModel.id)
        )
        r = await self._session.execute(q)
        return list(r.scalars().all())

    async def get_by_id(self, auth_user_id: int, rate_id: str) -> UserHourlyRateModel | None:
        r = await self._session.execute(
            select(UserHourlyRateModel).where(
                UserHourlyRateModel.auth_user_id == auth_user_id,
                UserHourlyRateModel.id == rate_id,
            )
        )
        return r.scalars().one_or_none()

    def _has_overlap(
        self,
        rows: list[UserHourlyRateModel],
        valid_from: date | None,
        valid_to: date | None,
        *,
        exclude_id: str | None = None,
    ) -> bool:
        for row in rows:
            if exclude_id and row.id == exclude_id:
                continue
            if intervals_overlap(row.valid_from, row.valid_to, valid_from, valid_to):
                return True
        return False

    async def create(
        self,
        *,
        auth_user_id: int,
        rate_kind: str,
        amount: Decimal,
        currency: str,
        valid_from: date | None,
        valid_to: date | None,
        applies_to_project_id: str | None = None,
    ) -> UserHourlyRateModel:
        if rate_kind not in _RATE_KINDS:
            raise ValueError("Недопустимый тип ставки")
        validate_range_order(valid_from, valid_to)
        if amount <= 0:
            raise ValueError("Сумма должна быть больше нуля")
        cur_norm = (currency or "USD").strip().upper()[:10] or "USD"
        existing = await self.list_by_user_and_kind(auth_user_id, rate_kind)
        same_cur = [
            r
            for r in existing
            if _rate_currency_key(r) == cur_norm and _applies_scope_match(r, applies_to_project_id)
        ]
        if self._has_overlap(same_cur, valid_from, valid_to):
            raise ValueError("Интервал пересекается с другой ставкой этого типа и валюты")
        now = _now_utc()
        row = UserHourlyRateModel(
            id=str(uuid.uuid4()),
            auth_user_id=auth_user_id,
            rate_kind=rate_kind,
            amount=amount,
            currency=cur_norm,
            valid_from=valid_from,
            valid_to=valid_to,
            applies_to_project_id=applies_to_project_id,
            created_at=now,
            updated_at=None,
        )
        self._session.add(row)
        return row

    async def update(
        self,
        *,
        auth_user_id: int,
        rate_id: str,
        patch: dict[str, Any],
    ) -> UserHourlyRateModel:
        row = await self.get_by_id(auth_user_id, rate_id)
        if not row:
            raise LookupError("not_found")

        new_amount: Decimal = row.amount
        if "amount" in patch:
            new_amount = _to_decimal(patch["amount"])
        if new_amount <= 0:
            raise ValueError("Сумма должна быть больше нуля")

        new_currency = row.currency
        if "currency" in patch:
            new_currency = (patch["currency"] or "USD").strip().upper()[:10] or "USD"

        new_from = patch["valid_from"] if "valid_from" in patch else row.valid_from
        new_to = patch["valid_to"] if "valid_to" in patch else row.valid_to

        validate_range_order(new_from, new_to)
        existing = await self.list_by_user_and_kind(auth_user_id, row.rate_kind)
        new_cur = (new_currency or "USD").strip().upper()[:10] or "USD"
        scope = getattr(row, "applies_to_project_id", None) or None
        others = [
            r
            for r in existing
            if r.id != rate_id
            and _rate_currency_key(r) == new_cur
            and _applies_scope_match(r, scope)
        ]
        if self._has_overlap(others, new_from, new_to):
            raise ValueError("Интервал пересекается с другой ставкой этого типа и валюты")

        row.amount = new_amount
        row.currency = new_currency
        row.valid_from = new_from
        row.valid_to = new_to
        row.updated_at = _now_utc()
        self._session.add(row)
        return row

    async def delete(self, auth_user_id: int, rate_id: str) -> bool:
        row = await self.get_by_id(auth_user_id, rate_id)
        if not row:
            return False
        await self._session.execute(
            delete(UserHourlyRateModel).where(
                UserHourlyRateModel.auth_user_id == auth_user_id,
                UserHourlyRateModel.id == rate_id,
            )
        )
        return True
