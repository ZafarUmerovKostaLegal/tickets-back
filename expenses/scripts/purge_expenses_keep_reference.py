"""Полная очистка заявок на расход (expense_requests) с сохранением справочников.

Удаляет ВСЁ прикладное содержимое — и мок, и боевые (production) данные:
  • все заявки expense_requests (+ вложения, история статусов, audit — каскадом);
  • файлы вложений в media/expenses/<id>/ (если каталог доступен);
  • сбрасывает счётчик номеров expense_kl_sequence.

Не трогает:
  • expense_types, expense_departments, expense_projects, exchange_rates;
  • пользователей auth.

=== Запуск на сервере БЕЗ Docker ===

  cd /path/to/tickets-back
  python3 -m venv .venv-purge && source .venv-purge/bin/activate
  pip install -r scripts/requirements-wipe.txt

  export EXPENSES_DATABASE_URL="postgresql://USER:PASS@HOST:5432/kosta_expenses"

  python scripts/purge_expenses_keep_reference.py --dry-run
  python scripts/purge_expenses_keep_reference.py --execute --confirm WIPE_EXPENSES_KEEP_REFERENCE

С явным URL:

  python scripts/purge_expenses_keep_reference.py --database-url postgresql://... --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from infrastructure.models import (
    ExpenseAttachmentModel,
    ExpenseAuditLogModel,
    ExpenseKlSequenceModel,
    ExpenseRequestModel,
    ExpenseStatusHistoryModel,
)

CONFIRM_PHRASE = "WIPE_EXPENSES_KEEP_REFERENCE"


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


async def _counts(session: AsyncSession) -> dict[str, int]:
    async def c(model) -> int:
        r = await session.execute(select(func.count()).select_from(model))
        return int(r.scalar_one() or 0)

    kl = 0
    r = await session.execute(select(ExpenseKlSequenceModel.last_seq).limit(1))
    row = r.scalar_one_or_none()
    if row is not None:
        kl = int(row)

    return {
        "expense_requests": await c(ExpenseRequestModel),
        "attachments": await c(ExpenseAttachmentModel),
        "status_history": await c(ExpenseStatusHistoryModel),
        "audit_logs": await c(ExpenseAuditLogModel),
        "kl_sequence_last_seq": kl,
    }


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


async def _purge(
    session: AsyncSession,
    *,
    dry_run: bool,
    media_root: Path | None,
) -> None:
    counts = await _counts(session)
    if counts["expense_requests"] == 0:
        print("Нет заявок expense_requests для удаления.")
        if not dry_run and counts["kl_sequence_last_seq"]:
            await session.execute(update(ExpenseKlSequenceModel).values(last_seq=0))
            await session.commit()
            print("Счётчик expense_kl_sequence сброшен в 0.")
        return

    print("Текущие объёмы (мок + боевые данные):")
    for k, v in counts.items():
        print(f"  {k}: {v}")

    print("\nБудут удалены ВСЕ строки из expense_requests (+ каскад attachments/history/audit).")
    print("Справочники (expense_types, departments, projects, exchange_rates) сохраняются.")

    if dry_run:
        print(
            f"\n[dry-run] Без изменений. Для удаления: "
            f"--execute --confirm {CONFIRM_PHRASE!r}"
        )
        return

    r = await session.execute(select(ExpenseRequestModel.id))
    expense_ids = [str(x[0]) for x in r.all()]

    print(f"\n*** УДАЛЕНИЕ: {len(expense_ids)} заявок на расход ***")
    _purge_media(expense_ids, media_root)

    await session.execute(delete(ExpenseRequestModel))
    await session.execute(update(ExpenseKlSequenceModel).values(last_seq=0))
    await session.commit()

    print("\nГотово: все заявки на расход удалены, справочники сохранены.")

    after = await _counts(session)
    print("\nПосле очистки:")
    for k, v in after.items():
        print(f"  {k}: {v}")


async def _run(
    database_url: str,
    *,
    dry_run: bool,
    media_path: str | None,
) -> int:
    media_root = _resolve_media_path(media_path)
    if media_root:
        print(f"Каталог вложений: {media_root}")

    engine = create_async_engine(_make_async_url(database_url), echo=False)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autocommit=False, autoflush=False
    )
    try:
        async with session_factory() as session:
            await _purge(session, dry_run=dry_run, media_root=media_root)
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Удалить ВСЕ заявки на расход (мок и боевые). "
            "Справочники expense_types / departments / projects сохраняются."
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
        "--confirm",
        type=str,
        default="",
        help=f"При --execute укажите дословно: {CONFIRM_PHRASE!r}",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="Только план и счётчики")
    g.add_argument("--execute", action="store_true", help="Выполнить удаление")

    args = p.parse_args(argv)
    if args.execute and args.confirm.strip() != CONFIRM_PHRASE:
        print(f"Для --execute укажите: --confirm {CONFIRM_PHRASE}", file=sys.stderr)
        return 1

    db_url = _resolve_database_url(args.database_url or None)
    dry = not args.execute
    return asyncio.run(
        _run(db_url, dry_run=dry, media_path=args.media_path or None)
    )


if __name__ == "__main__":
    raise SystemExit(main())
