
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, or_, select

from infrastructure.config import get_settings
from infrastructure.database import async_session_factory
from infrastructure.models import ExpenseRequestModel


def _where_prefix(prefix: str, *, match_all_text: bool):
    p = f"{prefix.strip()}%"
    if not match_all_text:
        return ExpenseRequestModel.description.ilike(p)
    return or_(
        ExpenseRequestModel.description.ilike(p),
        ExpenseRequestModel.comment.ilike(p),
        ExpenseRequestModel.vendor.ilike(p),
        ExpenseRequestModel.business_purpose.ilike(p),
    )


def _rmtree_expense_media(expense_id: str) -> None:
    root = Path(get_settings().media_path) / "expenses" / expense_id
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)


async def _run(*, prefix: str, match_all_text: bool, dry_run: bool) -> int:
    pr = prefix.strip()
    if not pr:
        print("Пустой префикс", file=sys.stderr)
        return 1

    cond = _where_prefix(pr, match_all_text=match_all_text)

    async with async_session_factory() as session:
        r = await session.execute(
            select(ExpenseRequestModel.id, ExpenseRequestModel.description)
            .where(cond)
            .order_by(ExpenseRequestModel.id)
        )
        rows = list(r.all())
        if not rows:
            print(f"Заявок с ilike {pr!r}% (как настроено) не найдено.")
            return 0

        n = len(rows)
        for eid, desc in rows:
            s = (desc or "").replace("\n", " ")
            line = s[:200] + ("…" if len(s) > 200 else "")
            print(f"  {eid}  {line!r}")

        print(f"\nИтого: {n} заявок на расход.")

        if dry_run:
            print("\n[dry-run] Без изменений. Для удаления: --execute")
            return 0

        for eid, _ in rows:
            _rmtree_expense_media(eid)

        await session.execute(delete(ExpenseRequestModel).where(cond))
        await session.commit()

        print("\nУдаление выполнено (БД + каталоги вложений при наличии).")
        return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Удалить тестовые заявки на расход по префиксу в полях (по умолчанию [mock] в description)."
    )
    p.add_argument(
        "--prefix",
        type=str,
        default="[mock]",
        help="Префикс для сравнения ilike (по умолчанию [mock]).",
    )
    p.add_argument(
        "--match-all-text",
        action="store_true",
        help="Искать префикс ещё и в comment, vendor, business_purpose.",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--execute", action="store_true")
    args = p.parse_args()
    dry = bool(args.dry_run) or not bool(args.execute)

    return asyncio.run(
        _run(
            prefix=args.prefix,
            match_all_text=bool(args.match_all_text),
            dry_run=dry,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
