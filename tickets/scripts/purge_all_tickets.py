"""Полная очистка IT-тикетов: все tickets + комментарии за всё время.

Не трогает auth/users.

=== Контейнер tickets (Portainer → Console) ===

  python scripts/purge_all_tickets.py --dry-run
  python scripts/purge_all_tickets.py --execute --confirm WIPE_ALL_TICKETS
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

from infrastructure.models import CommentModel, TicketModel

CONFIRM_PHRASE = "WIPE_ALL_TICKETS"


def _make_async_url(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("postgresql://"):
        return u.replace("postgresql://", "postgresql+asyncpg://", 1)
    return u


def _resolve_database_url(cli_url: str | None) -> str:
    if cli_url and cli_url.strip():
        return cli_url.strip()
    for key in ("TICKETS_DATABASE_URL", "DATABASE_URL"):
        val = (os.environ.get(key) or "").strip()
        if val:
            print(f"Подключение: env {key}")
            return val
    raise SystemExit(
        "Задайте URL БД tickets:\n"
        "  export TICKETS_DATABASE_URL='postgresql://user:pass@host:5432/kosta_tickets'\n"
        "или: --database-url postgresql://..."
    )


def _resolve_media_path(cli_path: str | None) -> Path | None:
    if cli_path and cli_path.strip():
        return Path(cli_path.strip())
    for key in ("TICKETS_MEDIA_PATH", "MEDIA_PATH"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return Path(val)
    try:
        from infrastructure.config import get_settings

        return Path(get_settings().media_path)
    except Exception:
        return None


def _purge_media(media_root: Path | None) -> None:
    if not media_root:
        return
    tickets_dir = media_root / "tickets"
    if tickets_dir.is_dir():
        shutil.rmtree(tickets_dir, ignore_errors=True)
        print(f"  Удалён каталог вложений: {tickets_dir}")


async def _purge(session: AsyncSession, *, dry_run: bool, media_root: Path | None) -> None:
    tickets_n = int(
        (await session.execute(select(func.count()).select_from(TicketModel))).scalar() or 0
    )
    comments_n = int(
        (await session.execute(select(func.count()).select_from(CommentModel))).scalar() or 0
    )

    print(f"  tickets: {tickets_n}")
    print(f"  ticket_comments: {comments_n}")

    if tickets_n == 0 and comments_n == 0:
        print("Нет данных для удаления.")
        return

    if dry_run:
        print(
            f"\n[dry-run] Без изменений. Для удаления: "
            f"--execute --confirm {CONFIRM_PHRASE!r}"
        )
        return

    print("\n*** УДАЛЕНИЕ всех IT-тикетов ***")
    _purge_media(media_root)
    await session.execute(delete(CommentModel))
    await session.execute(delete(TicketModel))
    await session.commit()
    print("Готово: все тикеты и комментарии удалены.")


async def _run(database_url: str, *, dry_run: bool, media_path: str | None) -> int:
    media_root = _resolve_media_path(media_path)
    if media_root:
        print(f"Каталог media: {media_root}")

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
    p = argparse.ArgumentParser(description="Удалить все IT-тикеты и комментарии за всё время.")
    p.add_argument("--database-url", type=str, default="", help="PostgreSQL URL")
    p.add_argument("--media-path", type=str, default="", help="Корень media")
    p.add_argument(
        "--confirm",
        type=str,
        default="",
        help=f"При --execute: {CONFIRM_PHRASE!r}",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--execute", action="store_true")

    args = p.parse_args(argv)
    if args.execute and args.confirm.strip() != CONFIRM_PHRASE:
        print(f"Для --execute укажите: --confirm {CONFIRM_PHRASE}", file=sys.stderr)
        return 1

    return asyncio.run(
        _run(
            _resolve_database_url(args.database_url or None),
            dry_run=not args.execute,
            media_path=args.media_path or None,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
