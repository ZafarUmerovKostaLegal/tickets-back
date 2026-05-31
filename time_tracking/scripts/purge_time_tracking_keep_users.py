"""Очистка данных time tracking с сохранением пользователей (time_tracking_users).

Удаляет:
  • записи времени, сдачи недель, разблокировки редактирования;
  • счета и счётчики номеров;
  • снимки/представления отчётов (включая партнёрские подтверждения — каскадом);
  • ставки пользователей, доступы к проектам;
  • клиентов и всё связанное (проекты, задачи, контакты, категории расходов).

Не трогает:
  • строки time_tracking_users (профили сотрудников в модуле TT);
  • пользователей auth (основное приложение).

Запуск из каталога time_tracking (нужен DATABASE_URL или TIME_TRACKING_DATABASE_URL):

  python scripts/purge_time_tracking_keep_users.py --dry-run
  python scripts/purge_time_tracking_keep_users.py --execute --confirm WIPE_TT_KEEP_USERS

С явным URL:

  python scripts/purge_time_tracking_keep_users.py --database-url postgresql://... --dry-run

Docker (профиль tools, из корня репозитория):

  docker compose --profile tools run --rm wipe_keep_tt \\
    python time_tracking/scripts/purge_time_tracking_keep_users.py --dry-run

Опционально удалить мок-пользователей TT (email mock.tt.user.*@local.invalid):

  ... --execute --confirm WIPE_TT_KEEP_USERS --remove-mock-users
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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

CONFIRM_PHRASE = "WIPE_TT_KEEP_USERS"

MOCK_EMAIL_PATTERN = "mock.tt.user.%@local.invalid"

TABLES_CLEARED = (
    "time_tracking_entries",
    "time_tracking_invoices (+ line_items, payments, audit_logs — каскадом)",
    "time_tracking_invoice_counters",
    "tt_report_snapshots (+ rows, partner confirmations — каскадом)",
    "tt_report_saved_views",
    "time_tracking_weekly_submissions",
    "time_tracking_time_entry_edit_unlocks",
    "time_tracking_user_project_access",
    "time_tracking_user_hourly_rates",
    "time_tracking_clients (+ projects, tasks, contacts, expense categories — каскадом)",
)


def _make_async_url(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("postgresql://"):
        return u.replace("postgresql://", "postgresql+asyncpg://", 1)
    return u


def _resolve_database_url(cli_url: str | None) -> str:
    if cli_url and cli_url.strip():
        return cli_url.strip()
    for key in ("TIME_TRACKING_DATABASE_URL", "DATABASE_URL"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    raise SystemExit(
        "Задайте URL БД: --database-url или env TIME_TRACKING_DATABASE_URL / DATABASE_URL"
    )


async def _counts(session: AsyncSession) -> dict[str, int]:
    async def c(model) -> int:
        r = await session.execute(select(func.count()).select_from(model))
        return int(r.scalar_one() or 0)

    mock_cond = TimeTrackingUserModel.email.like(MOCK_EMAIL_PATTERN)
    r_mock = await session.execute(
        select(func.count()).select_from(TimeTrackingUserModel).where(mock_cond)
    )
    r_real = await session.execute(
        select(func.count()).select_from(TimeTrackingUserModel).where(~mock_cond)
    )

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
        "tt_users_real": int(r_real.scalar_one() or 0),
        "tt_users_mock": int(r_mock.scalar_one() or 0),
    }


async def _purge(
    session: AsyncSession,
    *,
    dry_run: bool,
    remove_mock_users: bool,
) -> None:
    counts = await _counts(session)
    data_rows = sum(
        v
        for k, v in counts.items()
        if k not in ("tt_users_real", "tt_users_mock")
    )
    if data_rows == 0 and (not remove_mock_users or counts["tt_users_mock"] == 0):
        print("Нет данных для удаления.")
        return

    print("Текущие объёмы:")
    for k, v in counts.items():
        print(f"  {k}: {v}")

    print("\nБудут очищены таблицы:")
    for t in TABLES_CLEARED:
        print(f"  — {t}")
    print(f"\nОстанутся пользователи TT (не мок): {counts['tt_users_real']}")
    if remove_mock_users:
        print(f"Дополнительно удалятся мок-пользователи TT: {counts['tt_users_mock']}")
    else:
        print(f"Мок-пользователи TT не трогаем: {counts['tt_users_mock']}")

    if dry_run:
        print(f"\n[dry-run] Без изменений. Удаление: --execute --confirm {CONFIRM_PHRASE!r}")
        return

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

    if remove_mock_users and counts["tt_users_mock"]:
        mock_cond = TimeTrackingUserModel.email.like(MOCK_EMAIL_PATTERN)
        await session.execute(delete(TimeTrackingUserModel).where(mock_cond))

    await session.commit()
    print("\nГотово: данные time tracking удалены, пользователи TT сохранены.")


async def _run(
    database_url: str,
    *,
    dry_run: bool,
    remove_mock_users: bool,
) -> int:
    engine = create_async_engine(_make_async_url(database_url), echo=False)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autocommit=False, autoflush=False
    )
    try:
        async with session_factory() as session:
            await _purge(session, dry_run=dry_run, remove_mock_users=remove_mock_users)
    finally:
        await engine.dispose()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Удалить данные time tracking, сохранив time_tracking_users."
    )
    p.add_argument(
        "--database-url",
        type=str,
        default="",
        help="PostgreSQL URL (иначе TIME_TRACKING_DATABASE_URL или DATABASE_URL)",
    )
    p.add_argument(
        "--confirm",
        type=str,
        default="",
        help=f"При --execute укажите дословно: {CONFIRM_PHRASE!r}",
    )
    p.add_argument(
        "--remove-mock-users",
        action="store_true",
        help=f"Также удалить TT-пользователей с email {MOCK_EMAIL_PATTERN!r}",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="Только план и счётчики")
    g.add_argument("--execute", action="store_true", help="Выполнить удаление")

    args = p.parse_args()
    if args.execute and args.confirm.strip() != CONFIRM_PHRASE:
        print(f"Для --execute укажите: --confirm {CONFIRM_PHRASE}", file=sys.stderr)
        return 1

    db_url = _resolve_database_url(args.database_url or None)
    dry = not args.execute
    return asyncio.run(
        _run(db_url, dry_run=dry, remove_mock_users=bool(args.remove_mock_users))
    )


if __name__ == "__main__":
    raise SystemExit(main())
