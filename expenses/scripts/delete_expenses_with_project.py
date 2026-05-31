"""Удалить заявки на расход, привязанные к проектам (project_id заполнен).

Не трогает заявки без проекта (общие / департаментские расходы).

=== Запуск в контейнере expenses (Portainer → Console) ===

  python scripts/delete_expenses_with_project.py --dry-run
  python scripts/delete_expenses_with_project.py --execute --confirm DELETE_EXPENSES_WITH_PROJECT

=== На сервере без Docker ===

  export EXPENSES_DATABASE_URL="postgresql://..."
  python scripts/delete_expenses_with_project.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from infrastructure.models import ExpenseRequestModel

CONFIRM_PHRASE = "DELETE_EXPENSES_WITH_PROJECT"


def _make_async_url(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("postgresql://"):
        return u.replace("postgresql://", "postgresql+asyncpg://", 1)
    return u


def _resolve_database_url(cli_url: str | None) -> str:
    if cli_url and cli_url.strip():
        return cli_url.strip()
    for key in ("EXPENSES_DATABASE_URL", "DATABASE_URL"):
        val = (os.environ.get(key) or "").strip()
        if val:
            print(f"Подключение: env {key}")
            return val
    raise SystemExit(
        "Задайте URL БД expenses:\n"
        "  export EXPENSES_DATABASE_URL='postgresql://user:pass@host:5432/kosta_expenses'\n"
        "или: --database-url postgresql://..."
    )


def _resolve_media_path(cli_path: str | None) -> Path | None:
    if cli_path and cli_path.strip():
        return Path(cli_path.strip())
    for key in ("EXPENSES_MEDIA_PATH", "MEDIA_PATH"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return Path(val)
    try:
        from infrastructure.config import get_settings

        return Path(get_settings().media_path)
    except Exception:
        return None


def _project_linked_condition():
    return (
        ExpenseRequestModel.project_id.isnot(None)
        & (func.trim(ExpenseRequestModel.project_id) != "")
    )


def _purge_media(expense_ids: list[str], media_root: Path | None) -> None:
    if not media_root or not expense_ids:
        return
    expenses_dir = media_root / "expenses" if media_root.name != "expenses" else media_root
    if not expenses_dir.is_dir():
        print(f"  Каталог {expenses_dir} не найден — вложения на диске не удалялись.")
        return
    removed = 0
    for eid in expense_ids:
        folder = expenses_dir / eid
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    if removed:
        print(f"  Удалено каталогов вложений: {removed}")


async def _run(
    database_url: str,
    *,
    dry_run: bool,
    limit: int | None,
    media_path: str | None,
) -> int:
    cond = _project_linked_condition()
    media_root = _resolve_media_path(media_path)

    engine = create_async_engine(_make_async_url(database_url), echo=False)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autocommit=False, autoflush=False
    )
    try:
        async with session_factory() as session:
            total = int(
                (await session.execute(select(func.count()).select_from(ExpenseRequestModel).where(cond))).scalar()
                or 0
            )
            without_project = int(
                (
                    await session.execute(
                        select(func.count()).select_from(ExpenseRequestModel).where(~cond)
                    )
                ).scalar()
                or 0
            )

            q = (
                select(ExpenseRequestModel.id, ExpenseRequestModel.description, ExpenseRequestModel.project_id)
                .where(cond)
                .order_by(ExpenseRequestModel.created_at.asc())
            )
            if limit is not None and limit > 0:
                q = q.limit(limit)
            rows = list((await session.execute(q)).all())

            print(f"Заявок с project_id (будут удалены): {total}")
            print(f"Заявок без project_id (останутся): {without_project}")
            if limit:
                print(f"(Показано/обработано не более {limit} строк для этого запуска.)")

            for eid, desc, pid in rows[:50]:
                s = (desc or "").replace("\n", " ")
                line = s[:120] + ("…" if len(s) > 120 else "")
                print(f"  {eid}  project={pid!r}  {line!r}")
            if len(rows) > 50:
                print(f"  … ещё {len(rows) - 50} строк")

            if dry_run:
                print(
                    f"\n[dry-run] Без изменений. Для удаления: "
                    f"--execute --confirm {CONFIRM_PHRASE!r}"
                )
                return 0

            if not rows:
                print("Нечего удалять.")
                return 0

            batch_ids = [r[0] for r in rows]
            _purge_media(batch_ids, media_root)

            await session.execute(delete(ExpenseRequestModel).where(ExpenseRequestModel.id.in_(batch_ids)))
            await session.commit()

            print(f"\nУдалено заявок с project_id: {len(batch_ids)}")
            if limit and total > len(batch_ids):
                print("Запустите команду ещё раз без --limit, чтобы удалить оставшиеся.")
            return 0
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Удалить заявки на расход, привязанные к проектам клиентов "
            "(поле project_id не пустое). Заявки без проекта не трогаются."
        )
    )
    p.add_argument(
        "--database-url",
        type=str,
        default="",
        help="PostgreSQL URL (иначе EXPENSES_DATABASE_URL или DATABASE_URL)",
    )
    p.add_argument(
        "--media-path",
        type=str,
        default="",
        help="Корень media (иначе EXPENSES_MEDIA_PATH / MEDIA_PATH / настройки сервиса)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Удалить не более N заявок за один запуск.",
    )
    p.add_argument(
        "--confirm",
        type=str,
        default="",
        help=f"При --execute укажите дословно: {CONFIRM_PHRASE!r}",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--execute", action="store_true")

    args = p.parse_args(argv)
    if args.execute and args.confirm.strip() != CONFIRM_PHRASE:
        print(f"Для --execute укажите: --confirm {CONFIRM_PHRASE}", file=sys.stderr)
        return 1

    db_url = _resolve_database_url(args.database_url or None)
    return asyncio.run(
        _run(
            db_url,
            dry_run=not args.execute,
            limit=args.limit,
            media_path=args.media_path or None,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
