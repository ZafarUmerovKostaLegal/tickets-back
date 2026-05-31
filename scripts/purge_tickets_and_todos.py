#!/usr/bin/env python3
"""Полная очистка IT-тикетов и todos за всё время.

Запуск на сервере БЕЗ Docker (из корня репозитория):

  export TICKETS_DATABASE_URL="postgresql://..."
  export TODOS_DATABASE_URL="postgresql://..."

  python scripts/purge_tickets_and_todos.py --dry-run
  python scripts/purge_tickets_and_todos.py --execute --confirm WIPE_TICKETS_AND_TODOS

Контейнеры (после деплоя образов):

  tickets: python scripts/purge_all_tickets.py --execute --confirm WIPE_ALL_TICKETS
  todos:   python scripts/purge_all_todos.py --execute --confirm WIPE_ALL_TODOS
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TICKETS_SCRIPT = _REPO_ROOT / "tickets" / "scripts" / "purge_all_tickets.py"
_TODOS_SCRIPT = _REPO_ROOT / "todos" / "scripts" / "purge_all_todos.py"

CONFIRM_PHRASE = "WIPE_TICKETS_AND_TODOS"


def _load_dotenv() -> None:
    env_path = _REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except ImportError:
        pass


def _load_module(path: Path, name: str, service_dir: Path):
    sys.path.insert(0, str(service_dir))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        print(f"Не удалось загрузить: {path}", file=sys.stderr)
        raise SystemExit(1)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="Удалить все IT-тикеты и все todos.")
    p.add_argument(
        "--confirm",
        type=str,
        default="",
        help=f"При --execute: {CONFIRM_PHRASE!r}",
    )
    p.add_argument("--tickets-only", action="store_true", help="Только tickets")
    p.add_argument("--todos-only", action="store_true", help="Только todos")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--execute", action="store_true")
    args = p.parse_args()

    if args.execute and args.confirm.strip() != CONFIRM_PHRASE:
        print(f"Для --execute укажите: --confirm {CONFIRM_PHRASE}", file=sys.stderr)
        return 1

    _load_dotenv()
    mode = "--execute" if args.execute else "--dry-run"
    rc = 0

    if not args.todos_only:
        if not _TICKETS_SCRIPT.is_file():
            print(f"Не найден: {_TICKETS_SCRIPT}", file=sys.stderr)
            return 1
        print("=" * 60)
        print("IT-тикеты (tickets)")
        print("=" * 60)
        tickets_mod = _load_module(_TICKETS_SCRIPT, "purge_tickets_impl", _REPO_ROOT / "tickets")
        confirm = ["--confirm", "WIPE_ALL_TICKETS"] if args.execute else []
        rc = tickets_mod.main([mode, *confirm])
        if rc != 0:
            return rc

    if not args.tickets_only:
        if not _TODOS_SCRIPT.is_file():
            print(f"Не найден: {_TODOS_SCRIPT}", file=sys.stderr)
            return 1
        print("\n" + "=" * 60)
        print("Todos")
        print("=" * 60)
        todos_mod = _load_module(_TODOS_SCRIPT, "purge_todos_impl", _REPO_ROOT / "todos")
        confirm = ["--confirm", "WIPE_ALL_TODOS"] if args.execute else []
        rc = todos_mod.main([mode, *confirm])

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
