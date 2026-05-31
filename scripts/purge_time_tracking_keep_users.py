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

Удаляется без разбора мок/боевой:
  • все записи времени, все клиенты, проекты, задачи, ставки, счета, отчёты

Остаётся:
  • time_tracking_users (профили сотрудников), auth/users не трогается
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TT_SCRIPT = _REPO_ROOT / "time_tracking" / "scripts" / "purge_time_tracking_keep_users.py"


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
    if not _TT_SCRIPT.is_file():
        print(f"Не найден скрипт: {_TT_SCRIPT}", file=sys.stderr)
        raise SystemExit(1)
    _load_dotenv()
    sys.path.insert(0, str(_REPO_ROOT / "time_tracking"))
    spec = importlib.util.spec_from_file_location("purge_tt_impl", _TT_SCRIPT)
    if spec is None or spec.loader is None:
        print(f"Не удалось загрузить: {_TT_SCRIPT}", file=sys.stderr)
        raise SystemExit(1)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    raise SystemExit(mod.main())
