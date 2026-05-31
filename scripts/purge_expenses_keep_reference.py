#!/usr/bin/env python3
"""Полная очистка БД expenses: удалить ВСЕ заявки на расход, сохранить справочники.

Запуск на сервере БЕЗ Docker (из корня клонированного репозитория):

  cd /path/to/tickets-back

  python3 -m venv .venv-purge
  source .venv-purge/bin/activate          # Windows: .venv-purge\\Scripts\\activate
  pip install -r scripts/requirements-wipe.txt

  export EXPENSES_DATABASE_URL="postgresql://USER:PASSWORD@HOST:5432/kosta_expenses"

  python scripts/purge_expenses_keep_reference.py --dry-run
  python scripts/purge_expenses_keep_reference.py --execute --confirm WIPE_EXPENSES_KEEP_REFERENCE

Удаляется без разбора мок/боевой:
  • все expense_requests (+ вложения, история, audit)
  • файлы в media/expenses/ (если каталог доступен)

Остаётся:
  • expense_types, expense_departments, expense_projects, exchange_rates
  • auth/users не трогается

После очистки отчёт Build reports → Expenses в time tracking будет пустым.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
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


if __name__ == "__main__":
    if not _EXP_SCRIPT.is_file():
        print(f"Не найден скрипт: {_EXP_SCRIPT}", file=sys.stderr)
        raise SystemExit(1)
    _load_dotenv()
    sys.path.insert(0, str(_REPO_ROOT / "expenses"))
    spec = importlib.util.spec_from_file_location("purge_exp_impl", _EXP_SCRIPT)
    if spec is None or spec.loader is None:
        print(f"Не удалось загрузить: {_EXP_SCRIPT}", file=sys.stderr)
        raise SystemExit(1)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    raise SystemExit(mod.main())
