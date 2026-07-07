from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from application.entry_pricing import _billable_amount_for_entry
from application.report_builder import _load_user_rates
from application.user_initials import resolve_user_initials
from infrastructure.models import (
    TimeEntryModel,
    TimeManagerClientTaskModel,
    TimeTrackingUserModel,
)
from infrastructure.repository_entries import TimeEntryRepository

_Q6 = Decimal("0.000001")
_Q2 = Decimal("0.01")


def _norm_note(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _d(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v)) if v else Decimal(0)


def _hours_key(v: Decimal) -> str:
    return str(v.quantize(_Q6, rounding=ROUND_HALF_UP))


def _money_key(v: Decimal) -> str:
    return str(v.quantize(_Q2, rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class DuplicateKey:
    auth_user_id: int
    work_date: date
    task_id: str
    note_norm: str
    hours_key: str
    amount_key: str
    currency: str

    def as_group_id(self) -> str:
        return "|".join(
            (
                str(self.auth_user_id),
                self.work_date.isoformat(),
                self.task_id,
                self.note_norm,
                self.hours_key,
                self.amount_key,
                self.currency,
            )
        )


def _entry_to_duplicate_row(
    e: TimeEntryModel,
    *,
    u: TimeTrackingUserModel | None,
    t: TimeManagerClientTaskModel | None,
    project_currency: str,
    rates_map: dict[int, list],
    initials_map: dict[int, str | None] | None = None,
) -> tuple[DuplicateKey, dict[str, Any]]:
    hrs = _d(e.rounded_hours if e.rounded_hours is not None else e.hours)
    amt, cur = _billable_amount_for_entry(
        hrs,
        e.is_billable,
        e.work_date,
        rates_map.get(e.auth_user_id),
        project_currency=project_currency,
        time_entry_project_id=e.project_id,
    )
    key = DuplicateKey(
        auth_user_id=int(e.auth_user_id),
        work_date=e.work_date,
        task_id=(e.task_id or "").strip(),
        note_norm=_norm_note(e.description),
        hours_key=_hours_key(hrs),
        amount_key=_money_key(_d(amt)),
        currency=(cur or project_currency or "USD").strip()[:10],
    )
    user_name = (u.display_name or u.email or str(e.auth_user_id)) if u else str(e.auth_user_id)
    row = {
        "entry_id": e.id,
        "auth_user_id": int(e.auth_user_id),
        "user_name": user_name,
        "user_initials": resolve_user_initials(u, initials_map=initials_map or {}),
        "work_date": e.work_date.isoformat(),
        "task_id": e.task_id,
        "task_name": t.name if t else "",
        "description": (e.description or "").strip(),
        "hours": float(_d(e.hours)),
        "rounded_hours": float(hrs),
        "is_billable": bool(e.is_billable),
        "billable_amount": float(_d(amt)),
        "currency": key.currency,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }
    return key, row


async def find_duplicate_time_entries_for_project(
    session: AsyncSession,
    *,
    project_id: str,
    project_currency: str,
    date_from: date | None = None,
    date_to: date | None = None,
    users_map: dict[int, TimeTrackingUserModel] | None = None,
    tasks_map: dict[str, TimeManagerClientTaskModel] | None = None,
    initials_map: dict[int, str | None] | None = None,
    min_group_size: int = 2,
) -> dict[str, Any]:
    repo = TimeEntryRepository(session)
    entries = await repo.list_entries_for_project(project_id, date_from, date_to)
    if not entries:
        return {"groups": [], "summary": {"group_count": 0, "entry_count": 0, "user_count": 0}}

    if users_map is None:
        from sqlalchemy import select

        users_map = {
            u.auth_user_id: u
            for u in (await session.execute(select(TimeTrackingUserModel))).scalars().all()
        }
    if tasks_map is None:
        from sqlalchemy import select

        tasks_map = {
            t.id: t
            for t in (await session.execute(select(TimeManagerClientTaskModel))).scalars().all()
        }

    user_ids = sorted({e.auth_user_id for e in entries})
    rates_map = await _load_user_rates(session, user_ids)
    cur = (project_currency or "USD").strip()[:10] or "USD"

    groups: dict[DuplicateKey, list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        u = users_map.get(e.auth_user_id)
        t = tasks_map.get(e.task_id) if e.task_id else None
        key, row = _entry_to_duplicate_row(
            e,
            u=u,
            t=t,
            project_currency=cur,
            rates_map=rates_map,
            initials_map=initials_map,
        )
        groups[key].append(row)

    dup_groups = [g for g in groups.values() if len(g) >= min_group_size]
    dup_groups.sort(
        key=lambda rows: (
            -len(rows),
            rows[0].get("user_name") or "",
            rows[0].get("work_date") or "",
        )
    )

    out_groups: list[dict[str, Any]] = []
    for idx, rows in enumerate(dup_groups, start=1):
        first = rows[0]
        key = DuplicateKey(
            auth_user_id=int(first["auth_user_id"]),
            work_date=date.fromisoformat(str(first["work_date"])),
            task_id=(first.get("task_id") or "").strip(),
            note_norm=_norm_note(first.get("description")),
            hours_key=_hours_key(_d(first.get("rounded_hours"))),
            amount_key=_money_key(_d(first.get("billable_amount"))),
            currency=str(first.get("currency") or cur),
        )
        rows_sorted = sorted(rows, key=lambda r: (r.get("created_at") or "", r.get("entry_id") or ""))
        out_groups.append(
            {
                "group_id": key.as_group_id(),
                "group_label": f"DUP-{idx:04d}",
                "auth_user_id": key.auth_user_id,
                "user_name": first.get("user_name"),
                "user_initials": first.get("user_initials"),
                "work_date": first.get("work_date"),
                "task_id": first.get("task_id"),
                "task_name": first.get("task_name"),
                "description": first.get("description"),
                "rounded_hours": first.get("rounded_hours"),
                "billable_amount": first.get("billable_amount"),
                "currency": first.get("currency"),
                "entries_in_group": len(rows_sorted),
                "entries": rows_sorted,
            }
        )

    entry_count = sum(g["entries_in_group"] for g in out_groups)
    user_count = len({g["auth_user_id"] for g in out_groups})
    return {
        "groups": out_groups,
        "summary": {
            "group_count": len(out_groups),
            "entry_count": entry_count,
            "user_count": user_count,
        },
    }
