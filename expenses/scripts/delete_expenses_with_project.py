
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select

from infrastructure.config import get_settings
from infrastructure.database import async_session_factory
from infrastructure.models import ExpenseRequestModel


def _project_linked_condition():

    return (
        ExpenseRequestModel.project_id.isnot(None)
        & (func.trim(ExpenseRequestModel.project_id) != "")
    )


def _rmtree_expense_media(expense_id: str) -> None:
    root = Path(get_settings().media_path) / "expenses" / expense_id
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)


async def _run(*, dry_run: bool, limit: int | None) -> int:
    cond = _project_linked_condition()

    async with async_session_factory() as session:
        cnt_q = select(func.count()).select_from(ExpenseRequestModel).where(cond)
        total = int((await session.execute(cnt_q)).scalar() or 0)

        q = (
            select(ExpenseRequestModel.id, ExpenseRequestModel.description, ExpenseRequestModel.project_id)
            .where(cond)
            .order_by(ExpenseRequestModel.created_at.asc())
        )
        if limit is not None and limit > 0:
            q = q.limit(limit)
        rows = list((await session.execute(q)).all())

        print(f"Найдено заявок с заполненным project_id (привязка к проекту клиента): {total}")
        if limit:
            print(f"(Показано/обработано не более {limit} строк для этого запуска.)")

        for eid, desc, pid in rows:
            s = (desc or "").replace("\n", " ")
            line = s[:120] + ("…" if len(s) > 120 else "")
            print(f"  {eid}  project={pid!r}  {line!r}")

        if dry_run:
            print("\n[dry-run] Изменений нет. Для удаления: --execute")
            return 0

        if not rows:
            print("Нечего удалять.")
            return 0

        batch_ids = [r[0] for r in rows]

        for eid in batch_ids:
            _rmtree_expense_media(eid)

        await session.execute(delete(ExpenseRequestModel).where(ExpenseRequestModel.id.in_(batch_ids)))
        await session.commit()

        print(f"\nУдалено заявок: {len(batch_ids)} (вложения на диске при наличии каталогов).")
        if limit and total > len(batch_ids):
            print("Запустите команду ещё раз без --limit, чтобы удалить оставшиеся.")
        return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Удалить заявки на расход, привязанные к проектам клиентов "
            "(поле project_id не пустое). Заявки без проекта не трогаются."
        )
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Удалить не более N заявок за один запуск (остальное — повторным запуском).",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--execute", action="store_true")
    args = p.parse_args()
    dry = bool(args.dry_run)

    return asyncio.run(_run(dry_run=dry, limit=args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
