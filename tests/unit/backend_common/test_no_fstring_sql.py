# -*- coding: utf-8 -*-
"""Fail CI if application/infrastructure use f-string SQL (injection risk).

Defense-in-depth: SqlInjectionGuardMiddleware is NOT a substitute for
parameterized queries. This scan catches text(f"...") / execute(f"...") anti-patterns.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SCAN_DIRS = (
    "backend_common",
    "gateway",
    "auth",
    "time_tracking",
    "tickets",
    "expenses",
    "correspondence",
    "notifications",
    "inventory",
    "chat",
    "attendance",
    "vacation",
    "todos",
    "backup",
    "contacts",
    "call_schedule",
    "telegram_bot",
)
_SKIP_PARTS = ("/tests/", "\\tests\\", "/.venv/", "\\__pycache__\\")
_PATTERNS = (
    re.compile(r"""\btext\s*\(\s*f["']"""),
    re.compile(r"""\bexecute\s*\(\s*f["']"""),
    re.compile(r"""\bexecutemany\s*\(\s*f["']"""),
)


def _iter_py_files():
    for name in _SCAN_DIRS:
        base = _ROOT / name
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            s = str(path)
            if any(p in s for p in _SKIP_PARTS):
                continue
            yield path


def test_no_fstring_sql_in_services():
    offenders: list[str] = []
    for path in _iter_py_files():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for pat in _PATTERNS:
                if pat.search(line):
                    rel = path.relative_to(_ROOT).as_posix()
                    offenders.append(f"{rel}:{i}: {line.strip()}")
                    break
    assert not offenders, "f-string SQL found (use bind params):\n" + "\n".join(offenders[:50])
