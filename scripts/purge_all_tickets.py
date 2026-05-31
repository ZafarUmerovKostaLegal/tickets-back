#!/usr/bin/env python3
"""Launcher: purge_all_tickets.py — см. tickets/scripts/purge_all_tickets.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "tickets" / "scripts" / "purge_all_tickets.py"

if __name__ == "__main__":
    env_path = _REPO_ROOT / ".env"
    if env_path.is_file():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_path, override=False)
        except ImportError:
            pass
    sys.path.insert(0, str(_REPO_ROOT / "tickets"))
    spec = importlib.util.spec_from_file_location("purge_tickets_impl", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    raise SystemExit(mod.main())
