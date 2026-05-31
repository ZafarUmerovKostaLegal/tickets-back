#!/usr/bin/env python3
"""Полная очистка БД time tracking: удалить ВСЁ (мок и боевые данные), кроме time_tracking_users.

Запуск на сервере БЕЗ Docker (из корня клонированного репозитория):

  cd /path/to/tickets-back

  python3 -m venv .venv-purge
  source .venv-purge/bin/activate          # Windows: .venv-purge\\Scripts\\activate
  pip install -r scripts/requirements-wipe.txt

  export TIME_TRACKING_DATABASE_URL="postgresql://USER:PASSWORD@HOST:5432/kosta_time_tracking"

  python scripts/purge_time_tracking_keep_users.py --dry-run
  python scripts/purge_time_tracking_keep_users.py --execute --confirm WIPE_TT_KEEP_USERS

Опционально также очистить заявки на расход (отчёт Build reports → Expenses):

  export EXPENSES_DATABASE_URL="postgresql://USER:PASSWORD@HOST:5432/kosta_expenses"
  python scripts/purge_time_tracking_keep_users.py --execute --confirm WIPE_TT_KEEP_USERS --with-expenses

Удаляется без разбора мок/боевой:
  • все записи времени, все клиенты, проекты, задачи, ставки, счета, отчёты
  • с --with-expenses: все expense_requests (справочники expenses сохраняются)

Остаётся:
  • time_tracking_users (профили сотрудников), auth/users не трогается
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TT_SCRIPT = _REPO_ROOT / "time_tracking" / "scripts" / "purge_time_tracking_keep_users.py"
_EXP_SCRIPT = _REPO_ROOT / "expenses" / "scripts" / "purge_expenses_keep_reference.py"


def _load_dotenv() -> None:
    env_path = _REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except ImportError:
        pass


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        print(f"Не удалось загрузить: {path}", file=sys.stderr)
        raise SystemExit(1)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--with-expenses", action="store_true")
    pre_args, remaining = pre.parse_known_args()

    if not _TT_SCRIPT.is_file():
        print(f"Не найден скрипт: {_TT_SCRIPT}", file=sys.stderr)
        return 1

    _load_dotenv()
    sys.path.insert(0, str(_REPO_ROOT / "time_tracking"))
    tt_mod = _load_module(_TT_SCRIPT, "purge_tt_impl")
    rc = tt_mod.main(remaining)
    if rc != 0:
        return rc

    if not pre_args.with_expenses:
        return 0

    if not _EXP_SCRIPT.is_file():
        print(f"Не найден скрипт expenses: {_EXP_SCRIPT}", file=sys.stderr)
        return 1

    print("\n" + "=" * 60)
    print("Очистка expenses (--with-expenses)")
    print("=" * 60 + "\n")

    sys.path.insert(0, str(_REPO_ROOT / "expenses"))
    exp_mod = _load_module(_EXP_SCRIPT, "purge_exp_impl")

    exp_argv: list[str] = []
    tt_confirmed_execute = False
    i = 0
    while i < len(remaining):
        tok = remaining[i]
        if tok in ("--dry-run", "--execute"):
            exp_argv.append(tok)
            if tok == "--execute":
                tt_confirmed_execute = True
        elif tok == "--confirm" and i + 1 < len(remaining):
            i += 1
        elif tok == "--database-url" and i + 1 < len(remaining):
            exp_argv.extend(["--database-url", remaining[i + 1]])
            i += 1
        elif tok == "--media-path" and i + 1 < len(remaining):
            exp_argv.extend(["--media-path", remaining[i + 1]])
            i += 1
        elif tok == "--remove-mock-users":
            pass
        i += 1

    if "--dry-run" not in exp_argv and "--execute" not in exp_argv:
        exp_argv.append("--dry-run")

    if tt_confirmed_execute:
        exp_argv.extend(["--confirm", "WIPE_EXPENSES_KEEP_REFERENCE"])

    return exp_mod.main(exp_argv)


if __name__ == "__main__":
    raise SystemExit(main())
