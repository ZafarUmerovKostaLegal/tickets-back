#!/usr/bin/env python3
"""Удалить расходы, привязанные к проектам (project_id). Заявки без проекта не трогаются.

Контейнер expenses (Portainer → Console):

  python scripts/delete_expenses_with_project.py --dry-run
  python scripts/delete_expenses_with_project.py --execute --confirm DELETE_EXPENSES_WITH_PROJECT

Сервер без Docker (из корня репозитория):

  export EXPENSES_DATABASE_URL="postgresql://..."
  python scripts/delete_expenses_with_project.py --execute --confirm DELETE_EXPENSES_WITH_PROJECT
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "expenses" / "scripts" / "delete_expenses_with_project.py"


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
    if not _SCRIPT.is_file():
        print(f"Не найден скрипт: {_SCRIPT}", file=sys.stderr)
        raise SystemExit(1)
    _load_dotenv()
    sys.path.insert(0, str(_REPO_ROOT / "expenses"))
    spec = importlib.util.spec_from_file_location("delete_exp_proj_impl", _SCRIPT)
    if spec is None or spec.loader is None:
        print(f"Не удалось загрузить: {_SCRIPT}", file=sys.stderr)
        raise SystemExit(1)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    raise SystemExit(mod.main())
