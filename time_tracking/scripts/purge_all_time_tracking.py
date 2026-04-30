"""Полная очистка данных time tracking в текущей БД (см. TIME_TRACKING_TABLES).

Не трогает пользователей основного приложения (auth) — только таблицы модуля time_tracking.

Запуск из каталога time_tracking:

  python scripts/purge_all_time_tracking.py --dry-run
  python scripts/purge_all_time_tracking.py --execute --confirm WIPE_ALL_TIME_TRACKING_DATA
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select

from infrastructure.database import async_session_factory
from infrastructure.models import (
    TimeEntryModel,
    TimeEntryEditUnlockModel,
    TimeManagerClientModel,
    TimeTrackingUserModel,
    TimeTrackingUserProjectAccessModel,
    UserHourlyRateModel,
    WeeklyTimeSubmissionModel,
)
from infrastructure.models_invoices import InvoiceCounterModel, InvoiceModel
from infrastructure.models_reports import ReportSavedViewModel, ReportSnapshotModel

CONFIRM_PHRASE = "WIPE_ALL_TIME_TRACKING_DATA"

TIME_TRACKING_TABLES = (
    "time_tracking_entries",
    "time_tracking_invoice_line_items / payments / audit_logs (каскадно со счетами)",
    "time_tracking_invoices",
    "time_tracking_invoice_counters",
    "tt_report_snapshot_rows / partner confirmations (каскадно со снимками)",
    "tt_report_snapshots",
    "tt_report_saved_views",
    "time_tracking_weekly_submissions",
    "time_tracking_time_entry_edit_unlocks",
    "time_tracking_user_project_access",
    "time_tracking_user_hourly_rates",
    "time_tracking_clients (+ контакты, задачи, категории расходов, проекты)",
    "time_tracking_users",
)


async def _counts(session) -> dict[str, int]:
    async def c(model) -> int:
        r = await session.execute(select(func.count()).select_from(model))
        return int(r.scalar_one() or 0)

    return {
        "entries": await c(TimeEntryModel),
        "invoices": await c(InvoiceModel),
        "invoice_counters": await c(InvoiceCounterModel),
        "report_snapshots": await c(ReportSnapshotModel),
        "report_saved_views": await c(ReportSavedViewModel),
        "weekly_submissions": await c(WeeklyTimeSubmissionModel),
        "entry_edit_unlocks": await c(TimeEntryEditUnlockModel),
        "user_project_access": await c(TimeTrackingUserProjectAccessModel),
        "hourly_rates": await c(UserHourlyRateModel),
        "clients": await c(TimeManagerClientModel),
        "tt_users": await c(TimeTrackingUserModel),
    }


async def _run(*, dry_run: bool) -> int:
    async with async_session_factory() as session:
        counts = await _counts(session)
        total = sum(counts.values())
        if total == 0:
            print("В таблицах time tracking нет строк — нечего удалять.")
            return 0

        print("Текущие объёмы:")
        for k, v in counts.items():
            print(f"  {k}: {v}")
        print(f"\nВсего строк (по верхнеуровневым таблицам): {total}")
        print("\nБудут очищены таблицы:")
        for t in TIME_TRACKING_TABLES:
            print(f"  — {t}")

        if dry_run:
            print("\n[dry-run] Без изменений. Удаление: --execute --confirm", repr(CONFIRM_PHRASE))
            return 0

        await session.execute(delete(TimeEntryModel))
        await session.execute(delete(InvoiceModel))
        await session.execute(delete(InvoiceCounterModel))
        await session.execute(delete(ReportSnapshotModel))
        await session.execute(delete(ReportSavedViewModel))
        await session.execute(delete(WeeklyTimeSubmissionModel))
        await session.execute(delete(TimeEntryEditUnlockModel))
        await session.execute(delete(TimeTrackingUserProjectAccessModel))
        await session.execute(delete(UserHourlyRateModel))
        await session.execute(delete(TimeManagerClientModel))
        await session.execute(delete(TimeTrackingUserModel))

        await session.commit()
        print("\nГотово: все данные time tracking в этой БД удалены.")
        return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Удалить все данные модуля time tracking в подключённой БД."
    )
    p.add_argument(
        "--confirm",
        type=str,
        default="",
        help=f"При --execute укажите дословно: {CONFIRM_PHRASE!r}",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="Только план и счётчики")
    g.add_argument("--execute", action="store_true", help="Выполнить удаление")

    args = p.parse_args()
    if args.execute:
        if args.confirm.strip() != CONFIRM_PHRASE:
            print(
                f"Для --execute укажите: --confirm {CONFIRM_PHRASE}",
                file=sys.stderr,
            )
            return 1

    dry = not args.execute
    return asyncio.run(_run(dry_run=dry))


if __name__ == "__main__":
    raise SystemExit(main())
