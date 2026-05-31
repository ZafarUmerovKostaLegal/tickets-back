"""Полная очистка todos: доски, карточки, интеграция Outlook.

Не трогает auth/users. После очистки пользователям нужно будет заново подключить Outlook.

=== Контейнер todos (Portainer → Console) ===

  python scripts/purge_all_todos.py --dry-run
  python scripts/purge_all_todos.py --execute --confirm WIPE_ALL_TODOS
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

from infrastructure.models import (
    OutlookCalendarTokenModel,
    TodoBoardModel,
    TodoCardModel,
    TodoUserPreferenceModel,
)

CONFIRM_PHRASE = "WIPE_ALL_TODOS"


def _make_async_url(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("postgresql://"):
        return u.replace("postgresql://", "postgresql+asyncpg://", 1)
    return u


def _resolve_database_url(cli_url: str | None) -> str:
    if cli_url and cli_url.strip():
        return cli_url.strip()
    for key in ("TODOS_DATABASE_URL", "DATABASE_URL"):
        val = (os.environ.get(key) or "").strip()
        if val:
            print(f"Подключение: env {key}")
            return val
    raise SystemExit(
        "Задайте URL БД todos:\n"
        "  export TODOS_DATABASE_URL='postgresql://user:pass@host:5432/kosta_todos'\n"
        "или: --database-url postgresql://..."
    )


def _resolve_media_path(cli_path: str | None) -> Path | None:
    if cli_path and cli_path.strip():
        return Path(cli_path.strip())
    for key in ("TODOS_MEDIA_PATH", "MEDIA_PATH"):
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
    removed = 0
    for name in ("todo_cards", "todo_board_backgrounds"):
        folder = media_root / name
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
            print(f"  Удалён каталог: {folder}")
    if not removed:
        print(f"  Каталоги todo_cards / todo_board_backgrounds в {media_root} не найдены.")


async def _counts(session: AsyncSession) -> dict[str, int]:
    async def c(model) -> int:
        r = await session.execute(select(func.count()).select_from(model))
        return int(r.scalar_one() or 0)

    return {
        "boards": await c(TodoBoardModel),
        "cards": await c(TodoCardModel),
        "outlook_tokens": await c(OutlookCalendarTokenModel),
        "user_preferences": await c(TodoUserPreferenceModel),
    }


async def _purge(session: AsyncSession, *, dry_run: bool, media_root: Path | None) -> None:
    counts = await _counts(session)
    print("Текущие объёмы:")
    for k, v in counts.items():
        print(f"  {k}: {v}")

    if sum(counts.values()) == 0:
        print("Нет данных для удаления.")
        return

    if dry_run:
        print(
            f"\n[dry-run] Без изменений. Для удаления: "
            f"--execute --confirm {CONFIRM_PHRASE!r}"
        )
        return

    print("\n*** УДАЛЕНИЕ всех досок todos (+ колонки, карточки, вложения — каскадом) ***")
    _purge_media(media_root)
    await session.execute(delete(TodoUserPreferenceModel))
    await session.execute(delete(OutlookCalendarTokenModel))
    await session.execute(delete(TodoBoardModel))
    await session.commit()
    print("Готово: todos очищены.")


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
    p = argparse.ArgumentParser(description="Удалить все доски todos, карточки и токены Outlook.")
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
