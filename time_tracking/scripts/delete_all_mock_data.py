"""Удаление всех данных, созданных скриптом seed_tt_mock_data.py.

Time tracking (та же БД, что и сервис TT):
  • снимки отчётов и строки (в т.ч. партнёрские подтверждения — каскадом со снимка);
  • сохранённые представления отчётов;
  • записи времени по проектам мок-клиентов;
  • счета мок-клиентов;
  • клиенты с именем ilike префиксу (по умолчанию «[mock]»);
  • пользователи TT с email вида mock.tt.user.*@local.invalid (каскадом — ставки, доступы, сдачи недель и т.д.).

Сервис expenses (опционально, отдельная БД):
  • заявки с текстом описания, содержащим «[mock]» (как создаёт сидер).

Запуск из каталога time_tracking:

  python scripts/delete_all_mock_data.py --dry-run
  python scripts/delete_all_mock_data.py --execute

С расходами (ENV или флаг):

  EXPENSES_DATABASE_URL=... python scripts/delete_all_mock_data.py --execute --with-expenses

Docker (Linux, путь /app/scripts/...):

  python scripts/delete_all_mock_data.py --execute --with-expenses
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_REPO_ROOT = Path(__file__).resolve().parents[2]

from sqlalchemy import delete, func, or_, select

from infrastructure.database import async_session_factory
from infrastructure.models import (
    TimeEntryModel,
    TimeManagerClientModel,
    TimeManagerClientProjectModel,
    TimeTrackingUserModel,
)
from infrastructure.models_invoices import InvoiceModel
from infrastructure.models_reports import ReportSavedViewModel, ReportSnapshotModel


def _async_pg(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


async def _delete_tt_mock(
    *,
    session,
    prefix: str,
    dry_run: bool,
) -> dict[str, int]:
    pr = prefix.strip()
    if not pr:
        raise ValueError("Пустой префикс имени клиента")

    mock_email_cond = TimeTrackingUserModel.email.like("mock.tt.user.%@local.invalid")

    r_users = await session.execute(
        select(TimeTrackingUserModel.auth_user_id, TimeTrackingUserModel.email).where(mock_email_cond)
    )
    mock_user_rows = list(r_users.all())
    mock_uids = [int(row[0]) for row in mock_user_rows]

    r_clients = await session.execute(
        select(TimeManagerClientModel.id, TimeManagerClientModel.name)
        .where(TimeManagerClientModel.name.ilike(f"{pr}%"))
        .order_by(TimeManagerClientModel.name)
    )
    clients = list(r_clients.all())
    client_ids = [row[0] for row in clients]

    project_ids: list[str] = []
    if client_ids:
        rp = await session.execute(
            select(TimeManagerClientProjectModel.id).where(
                TimeManagerClientProjectModel.client_id.in_(client_ids)
            )
        )
        project_ids = [x[0] for x in rp.all()]

    n_entries_proj = 0
    if project_ids:
        qc = await session.execute(
            select(func.count())
            .select_from(TimeEntryModel)
            .where(TimeEntryModel.project_id.in_(project_ids))
        )
        n_entries_proj = int(qc.scalar_one() or 0)

    n_invoices = 0
    if client_ids:
        qi = await session.execute(
            select(func.count()).select_from(InvoiceModel).where(InvoiceModel.client_id.in_(client_ids))
        )
        n_invoices = int(qi.scalar_one() or 0)

    snap_conds: list = [ReportSnapshotModel.name.ilike(f"{pr}%")]
    if mock_uids:
        snap_conds.append(ReportSnapshotModel.created_by_user_id.in_(mock_uids))
    snap_cond = or_(*snap_conds) if len(snap_conds) > 1 else snap_conds[0]

    qs = await session.execute(select(func.count()).select_from(ReportSnapshotModel).where(snap_cond))
    n_snapshots = int(qs.scalar_one() or 0)

    view_conds: list = [ReportSavedViewModel.name.ilike(f"{pr}%")]
    if mock_uids:
        view_conds.append(ReportSavedViewModel.owner_user_id.in_(mock_uids))
    view_cond = or_(*view_conds) if len(view_conds) > 1 else view_conds[0]
    qv = await session.execute(select(func.count()).select_from(ReportSavedViewModel).where(view_cond))
    n_views = int(qv.scalar_one() or 0)

    stats = {
        "mock_tt_users": len(mock_uids),
        "mock_clients": len(clients),
        "mock_projects": len(project_ids),
        "time_entries_on_mock_projects": n_entries_proj,
        "invoices_mock_clients": n_invoices,
        "report_snapshots": n_snapshots,
        "report_saved_views": n_views,
    }

    print(f"Префикс клиентов: {pr!r}")
    for uid, em in mock_user_rows[:50]:
        print(f"  пользователь TT: auth_user_id={uid}  {em!r}")
    if len(mock_user_rows) > 50:
        print(f"  … ещё пользователей: {len(mock_user_rows) - 50}")
    for cid, name in clients[:80]:
        print(f"  клиент: {name!r} ({cid})")
    if len(clients) > 80:
        print(f"  … ещё клиентов: {len(clients) - 80}")

    print(
        f"\nИтого TT: пользователей-моков={stats['mock_tt_users']}, клиентов={stats['mock_clients']}, "
        f"проектов={stats['mock_projects']}, записей времени (на проектах мок)={stats['time_entries_on_mock_projects']}, "
        f"счетов={stats['invoices_mock_clients']}, снимков отчётов={stats['report_snapshots']}, "
        f"сохранённых видов={stats['report_saved_views']}."
    )

    if dry_run:
        print("\n[dry-run] Изменений нет. Для удаления: --execute")
        return stats

    await session.execute(delete(ReportSnapshotModel).where(snap_cond))
    await session.execute(delete(ReportSavedViewModel).where(view_cond))

    if project_ids:
        await session.execute(delete(TimeEntryModel).where(TimeEntryModel.project_id.in_(project_ids)))
    if client_ids:
        await session.execute(delete(InvoiceModel).where(InvoiceModel.client_id.in_(client_ids)))
        await session.execute(delete(TimeManagerClientModel).where(TimeManagerClientModel.id.in_(client_ids)))

    await session.execute(delete(TimeTrackingUserModel).where(mock_email_cond))

    await session.commit()
    print("\nУдаление данных TT выполнено.")
    return stats


async def _delete_expenses_mock(*, expenses_db_url: str, dry_run: bool) -> int:
    exp_root = _REPO_ROOT / "expenses"
    if not exp_root.is_dir():
        print("[expenses] каталог expenses/ не найден — пропуск.", file=sys.stderr)
        return 0

    sys.path.insert(0, str(exp_root))
    try:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        from infrastructure.models import ExpenseRequestModel  # noqa: PLC0415
    except ImportError as exc:
        print(f"[expenses] импорт: {exc}", file=sys.stderr)
        return 0

    pattern_desc = "%[mock]%"

    engine = create_async_engine(_async_pg(expenses_db_url), echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    deleted = 0
    try:
        async with factory() as s:
            qc = await s.execute(
                select(func.count()).select_from(ExpenseRequestModel).where(
                    ExpenseRequestModel.description.ilike(pattern_desc)
                )
            )
            n = int(qc.scalar_one() or 0)
            print(f"\nExpenses: заявок с описанием ilike {pattern_desc!r}: {n}")
            if dry_run:
                print("[dry-run] Заявки expenses не удалены.")
                return 0
            if n == 0:
                await s.commit()
                return 0
            res = await s.execute(
                delete(ExpenseRequestModel).where(ExpenseRequestModel.description.ilike(pattern_desc))
            )
            deleted = res.rowcount if res.rowcount is not None else n
            await s.commit()
            print(f"Удалено заявок expenses: {deleted}.")
    finally:
        await engine.dispose()
        try:
            sys.path.remove(str(exp_root))
        except ValueError:
            pass
    return int(deleted or 0)


async def _run(
    *,
    prefix: str,
    dry_run: bool,
    with_expenses: bool,
    expenses_database_url: str | None,
) -> int:
    async with async_session_factory() as session:
        await _delete_tt_mock(session=session, prefix=prefix, dry_run=dry_run)

    exp_url = (expenses_database_url or "").strip()
    if with_expenses and exp_url:
        await _delete_expenses_mock(expenses_db_url=exp_url, dry_run=dry_run)
    elif with_expenses and not exp_url:
        print(
            "\n[expenses] указан --with-expenses, но нет URL (EXPENSES_DATABASE_URL или --expenses-database-url).",
            file=sys.stderr,
        )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Удалить все мок-данные TT (+ опционально расходы с меткой [mock] в описании)."
    )
    p.add_argument(
        "--prefix",
        type=str,
        default="[mock]",
        help="Префикс имени клиента TT и имён снимков/видов (ilike).",
    )
    p.add_argument(
        "--with-expenses",
        action="store_true",
        help="Также удалить заявки в БД expenses с «[mock]» в описании.",
    )
    p.add_argument(
        "--expenses-database-url",
        type=str,
        default="",
        metavar="URL",
        help="PostgreSQL для expenses; если пусто — переменная окружения EXPENSES_DATABASE_URL.",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--execute", action="store_true")
    args = p.parse_args()

    dry = bool(args.dry_run)
    exp_merged = (args.expenses_database_url or "").strip() or os.environ.get(
        "EXPENSES_DATABASE_URL", ""
    ).strip()

    return asyncio.run(
        _run(
            prefix=args.prefix,
            dry_run=dry,
            with_expenses=bool(args.with_expenses),
            expenses_database_url=exp_merged or None,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
