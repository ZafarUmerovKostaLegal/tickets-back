#!/usr/bin/env python3
"""
Проверка, почему запись time_tracking_entries может не попадать в отчёт.

Пример:
  python time_tracking/scripts/diagnose_time_entry_in_reports.py \\
    --entry-id 6ce0a004-9110-461a-bc44-b1efcb8a50d0 \\
    --date-from 2026-06-01 --date-to 2026-06-30
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TT = ROOT / "time_tracking"
if str(TT) not in sys.path:
    sys.path.insert(0, str(TT))

from sqlalchemy import and_, select

from application.report_builder import _base_entry_conditions, load_week_submitted_user_dates
from infrastructure.database import async_session_factory
from infrastructure.models import (
    TimeEntryModel,
    TimeManagerClientProjectModel,
    TimeTrackingUserModel,
    WeeklyTimeSubmissionModel,
)
from infrastructure.repositories import ClientProjectRepository


def _parse_date(s: str) -> date:
    return date.fromisoformat(s.strip()[:10])


async def _run(
    *,
    entry_id: str | None,
    auth_user_id: int | None,
    work_date: date | None,
    date_from: date,
    date_to: date,
) -> int:
    async with async_session_factory() as session:
        row: TimeEntryModel | None = None
        if entry_id:
            row = (
                await session.execute(
                    select(TimeEntryModel).where(TimeEntryModel.id == entry_id.strip())
                )
            ).scalars().one_or_none()
        elif auth_user_id is not None and work_date is not None:
            rows = list(
                (
                    await session.execute(
                        select(TimeEntryModel).where(
                            TimeEntryModel.auth_user_id == auth_user_id,
                            TimeEntryModel.work_date == work_date,
                            TimeEntryModel.voided_at.is_(None),
                        )
                    )
                ).scalars().all()
            )
            if len(rows) == 1:
                row = rows[0]
            elif rows:
                print(f"Найдено {len(rows)} активных записей за {work_date} user={auth_user_id}:")
                for r in rows:
                    print(f"  - {r.id} project={r.project_id} hours={r.hours}")
                row = rows[0]
                print(f"Диагностика ниже — для первой: {row.id}")
            else:
                print("Активных записей не найдено (все voided или нет строк).")
                return 1
        else:
            print("Укажите --entry-id или пару --auth-user-id + --work-date")
            return 2

        if row is None:
            print(f"Запись не найдена: {entry_id!r}")
            return 1

        print("=== Запись ===")
        print(f"id:              {row.id}")
        print(f"auth_user_id:    {row.auth_user_id}")
        print(f"work_date:       {row.work_date}")
        print(f"hours:           {row.hours} ({row.duration_seconds}s)")
        print(f"is_billable:     {row.is_billable}")
        print(f"voided_at:       {row.voided_at}")
        print(f"void_kind:       {row.void_kind}")
        print(f"project_id:      {row.project_id}")
        print(f"task_id:         {row.task_id}")

        tt_user = (
            await session.execute(
                select(TimeTrackingUserModel).where(
                    TimeTrackingUserModel.auth_user_id == row.auth_user_id
                )
            )
        ).scalars().one_or_none()
        print("\n=== Пользователь TT ===")
        if tt_user:
            print(f"display_name:    {tt_user.display_name}")
            print(f"is_archived:     {tt_user.is_archived}")
            print(f"is_blocked:      {tt_user.is_blocked}")
        else:
            print("НЕ НАЙДЕН в time_tracking_users (FK при создании обычно не даст такого)")

        proj = None
        if row.project_id:
            cpr = ClientProjectRepository(session)
            proj = await cpr.get_by_id_global(row.project_id)
        print("\n=== Проект ===")
        if proj:
            print(f"name:            {proj.name}")
            print(f"code:            {proj.code}")
            print(f"project_type:    {proj.project_type}")
            print(f"is_archived:     {proj.is_archived}")
            print(f"client_id:       {proj.client_id}")
        else:
            print("Проект не найден (orphan project_id — в отчёте всё равно учитывается по id)")

        print(f"\n=== Период отчёта {date_from} .. {date_to} ===")
        reasons: list[str] = []
        if row.work_date < date_from or row.work_date > date_to:
            reasons.append("work_date вне диапазона from/to отчёта")
        if row.voided_at is not None:
            reasons.append(
                "voided_at задан — в суммах отчёта НЕ учитывается "
                "(попадает только в meta.voidedTimeEntries)"
            )
        if proj and proj.project_type == "fixed_fee":
            reasons.append(
                "проект fixed_fee — исключается если include_fixed_fee=false в запросе отчёта"
            )

        cond = _base_entry_conditions(
            date_from, date_to, [row.auth_user_id], None, None, include_fixed_fee=True
        )
        cond.append(TimeEntryModel.id == row.id)
        in_report = (
            await session.execute(select(TimeEntryModel.id).where(and_(*cond)))
        ).scalar_one_or_none()
        print(f"Попадает в SQL отчёта (include_fixed_fee=true): {'ДА' if in_report else 'НЕТ'}")

        cond_ff = _base_entry_conditions(
            date_from, date_to, [row.auth_user_id], None, None, include_fixed_fee=False
        )
        cond_ff.append(TimeEntryModel.id == row.id)
        in_report_ff = (
            await session.execute(select(TimeEntryModel.id).where(and_(*cond_ff)))
        ).scalar_one_or_none()
        print(f"Попадает при include_fixed_fee=false:              {'ДА' if in_report_ff else 'НЕТ'}")

        week_set = await load_week_submitted_user_dates(
            session, {row.auth_user_id}, date_from, date_to
        )
        submitted = (row.auth_user_id, row.work_date) in week_set
        print(f"\n=== Сдача недели (Sat..Fri) ===")
        print(f"is_week_submitted для дня: {'ДА' if submitted else 'НЕТ'}")
        if not submitted:
            wsubs = list(
                (
                    await session.execute(
                        select(WeeklyTimeSubmissionModel).where(
                            WeeklyTimeSubmissionModel.auth_user_id == row.auth_user_id,
                            WeeklyTimeSubmissionModel.week_end >= date_from,
                            WeeklyTimeSubmissionModel.week_start <= date_to,
                        )
                    )
                ).scalars().all()
            )
            if not wsubs:
                print("  Записей в time_tracking_weekly_submissions за период нет.")
                print("  Кнопка «Отправить на утверждение» на фронте пока без API — сдача только auto (celery).")
            else:
                for w in wsubs:
                    print(
                        f"  week {w.week_start}..{w.week_end} status={w.status} "
                        f"auto={w.auto_submitted_at is not None}"
                    )

        print("\n=== Итог ===")
        if reasons:
            for r in reasons:
                print(f"  • {r}")
        elif in_report:
            print("  Запись должна быть в отчёте времени. Если в UI пусто — проверьте:")
            print("  • фильтр сотрудников в отчёте (selectedUserIds в localStorage)")
            print("  • чекбокс «Включить fixed-fee»")
            print("  • кастомный период dateFrom/dateTo в localStorage")
            print("  • группировку: строка в проекте, детали — после раскрытия строки")
        else:
            print("  Запись не проходит фильтры отчёта — см. пункты выше.")

        return 0 if in_report or row.voided_at is not None else 1


def main() -> None:
    p = argparse.ArgumentParser(description="Диагностика попадания time entry в отчёт")
    p.add_argument("--entry-id")
    p.add_argument("--auth-user-id", type=int)
    p.add_argument("--work-date", type=_parse_date)
    p.add_argument("--date-from", type=_parse_date, default=_parse_date(os.environ.get("REPORT_FROM", "2026-06-01")))
    p.add_argument("--date-to", type=_parse_date, default=_parse_date(os.environ.get("REPORT_TO", "2026-06-30")))
    args = p.parse_args()
    raise SystemExit(
        asyncio.run(
            _run(
                entry_id=args.entry_id,
                auth_user_id=args.auth_user_id,
                work_date=args.work_date,
                date_from=args.date_from,
                date_to=args.date_to,
            )
        )
    )


if __name__ == "__main__":
    main()
