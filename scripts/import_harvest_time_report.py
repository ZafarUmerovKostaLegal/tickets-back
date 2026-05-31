#!/usr/bin/env python3
"""Импорт Harvest — запуск из корня tickets-back (без Docker).

  cd /path/to/tickets-back
  pip install -r time_tracking/requirements.txt
  export TIME_TRACKING_DATABASE_URL="postgresql://..."

  python scripts/import_harvest_time_report.py --dry-run
  python scripts/import_harvest_time_report.py --execute

Файл xlsx по умолчанию: harvest_time_report_from2023-01-23to2026-05-26.xlsx
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TT_SCRIPT = _REPO_ROOT / "time_tracking" / "scripts" / "import_harvest_time_report.py"


def _load_dotenv() -> None:
    env_path = _REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except ImportError:
        pass


def main() -> int:
    if not _TT_SCRIPT.is_file():
        print(f"Не найден скрипт: {_TT_SCRIPT}", file=sys.stderr)
        print("Выполните git pull в каталоге tickets-back.", file=sys.stderr)
        return 1
    _load_dotenv()
    spec = importlib.util.spec_from_file_location("harvest_import_impl", _TT_SCRIPT)
    if spec is None or spec.loader is None:
        print(f"Не удалось загрузить: {_TT_SCRIPT}", file=sys.stderr)
        return 1
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return int(mod.main())


if __name__ == "__main__":
    raise SystemExit(main())
