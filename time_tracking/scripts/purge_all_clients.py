
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, or_, select

from infrastructure.database import async_session_factory
from infrastructure.models import (
    TimeEntryModel,
    TimeManagerClientModel,
    TimeManagerClientProjectModel,
    TimeManagerClientTaskModel,
)
from infrastructure.models_invoices import InvoiceModel

CONFIRM_PHRASE = "DELETE_ALL_CLIENTS"


async def _run(*, dry_run: bool) -> int:
    async with async_session_factory() as session:
        r = await session.execute(
            select(TimeManagerClientModel.id, TimeManagerClientModel.name).order_by(
                TimeManagerClientModel.name
            )
        )
        clients = list(r.all())
        if not clients:
            print("В базе нет клиентов — нечего удалять.")
            return 0

        client_ids = [row[0] for row in clients]
        for _cid, name in clients:
            print(f"  клиент: {name!r}")

        rp = await session.execute(
            select(TimeManagerClientProjectModel.id).where(
                TimeManagerClientProjectModel.client_id.in_(client_ids)
            )
        )
        project_ids = [x[0] for x in rp.all()]

        rt = await session.execute(
            select(TimeManagerClientTaskModel.id).where(
                TimeManagerClientTaskModel.client_id.in_(client_ids)
            )
        )
        task_ids = [x[0] for x in rt.all()]

        conds = []
        if project_ids:
            conds.append(TimeEntryModel.project_id.in_(project_ids))
        if task_ids:
            conds.append(TimeEntryModel.task_id.in_(task_ids))
        n_entries = 0
        if conds:
            qc = await session.execute(
                select(func.count()).select_from(TimeEntryModel).where(or_(*conds))
            )
            n_entries = int(qc.scalar_one() or 0)

        qi = await session.execute(select(func.count()).select_from(InvoiceModel))
        n_invoices = int(qi.scalar_one() or 0)

        print(
            f"\nБудет удалено: {len(clients)} клиент(ов) (вся подчинённая иерархия), "
            f"{len(project_ids)} проект(ов), {len(task_ids)} задач(и) клиентов, "
            f"≈{n_entries} запис(ей) времени (по project_id / task_id), "
            f"{n_invoices} счет(ов) (все)."
        )

        if dry_run:
            print("\n[dry-run] Без изменений. Для удаления: --execute --confirm DELETE_ALL_CLIENTS")
            return 0

        if conds:
            await session.execute(delete(TimeEntryModel).where(or_(*conds)))
        await session.execute(delete(InvoiceModel))
        await session.execute(delete(TimeManagerClientModel))


        await session.execute(
            delete(TimeEntryModel).where(TimeEntryModel.project_id.isnot(None))
        )
        await session.commit()

        print("\nГотово: все клиенты и связанные данные удалены (см. список того, что не трогали — в docstring).")
        return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Удалить ВСЕХ клиентов time manager и связанные с ними проекты, счета и т.д."
    )
    p.add_argument(
        "--confirm",
        type=str,
        default="",
        help=f'Обязателен при --execute, дословно: {CONFIRM_PHRASE!r}',
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="Только план, без изменений")
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
