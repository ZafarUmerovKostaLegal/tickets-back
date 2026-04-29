
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
    TimeManagerClientModel,
    TimeManagerClientProjectModel,
)
from infrastructure.models_invoices import InvoiceModel


async def _run(*, prefix: str, dry_run: bool) -> int:
    async with async_session_factory() as session:
        pr = prefix.strip()
        if not pr:
            print("Пустой префикс", file=sys.stderr)
            return 1

        r = await session.execute(
            select(TimeManagerClientModel.id, TimeManagerClientModel.name)
            .where(TimeManagerClientModel.name.ilike(f"{pr}%"))
            .order_by(TimeManagerClientModel.name)
        )
        clients = list(r.all())
        if not clients:
            print(f"Клиенты с именем ilike {pr!r}% не найдены.")
            return 0

        client_ids = [row[0] for row in clients]
        for cid, name in clients:
            print(f"  клиент: {name!r} ({cid})")

        rp = await session.execute(
            select(TimeManagerClientProjectModel.id).where(
                TimeManagerClientProjectModel.client_id.in_(client_ids)
            )
        )
        project_ids = [x[0] for x in rp.all()]

        n_entries = 0
        if project_ids:
            qc = await session.execute(
                select(func.count())
                .select_from(TimeEntryModel)
                .where(TimeEntryModel.project_id.in_(project_ids))
            )
            n_entries = int(qc.scalar_one() or 0)

        qi = await session.execute(
            select(func.count()).select_from(InvoiceModel).where(InvoiceModel.client_id.in_(client_ids))
        )
        n_invoices = int(qi.scalar_one() or 0)

        print(
            f"\nИтого: {len(clients)} клиент(ов), {len(project_ids)} проект(ов), "
            f"{n_entries} запис(ей) времени, {n_invoices} счет(ов)."
        )

        if dry_run:
            print("\n[dry-run] Данные не изменены. Запустите с --execute для удаления.")
            return 0

        if project_ids:
            await session.execute(
                delete(TimeEntryModel).where(TimeEntryModel.project_id.in_(project_ids))
            )
        await session.execute(
            delete(InvoiceModel).where(InvoiceModel.client_id.in_(client_ids))
        )
        await session.execute(
            delete(TimeManagerClientModel).where(TimeManagerClientModel.id.in_(client_ids))
        )
        await session.commit()

        print("\nУдаление выполнено.")
        return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Удалить клиентов time tracking с именем, начинающимся с префикса (мок-данные)."
    )
    p.add_argument(
        "--prefix",
        type=str,
        default="[mock]",
        help="Префикс имени клиента (по умолчанию [mock], сравнение без учёта регистра: ilike).",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, что будет удалено (рекомендуется сначала).",
    )
    g.add_argument(
        "--execute",
        action="store_true",
        help="Выполнить удаление в БД.",
    )
    args = p.parse_args()
    dry = bool(args.dry_run) or not bool(args.execute)

    return asyncio.run(_run(prefix=args.prefix, dry_run=dry))


if __name__ == "__main__":
    raise SystemExit(main())
