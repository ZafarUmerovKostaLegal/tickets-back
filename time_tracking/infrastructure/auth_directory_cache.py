"""In-process TTL caches that cut time_tracking_db / auth read pressure.

No business rows are deleted — only short-lived responses / directory lookups.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any


class _TTLStore:
    def __init__(self, *, ttl: float, max_entries: int = 64) -> None:
        self._ttl = ttl
        self._max = max_entries
        self._data: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, exp = entry
        if time.monotonic() > exp:
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        if len(self._data) >= self._max:
            now = time.monotonic()
            expired = [k for k, (_, exp) in self._data.items() if now > exp]
            for k in expired:
                self._data.pop(k, None)
            while len(self._data) >= self._max and self._data:
                self._data.pop(next(iter(self._data)))
        self._data[key] = (value, time.monotonic() + self._ttl)

    def clear(self) -> None:
        self._data.clear()


AUTH_USERS_HINTS_CACHE = _TTLStore(ttl=120.0, max_entries=32)
PARTNERS_BY_PROJECT_CACHE = _TTLStore(ttl=45.0, max_entries=128)
# Pending/confirmed list JSON — short TTL so mutates + invalidate stay fresh enough.
PENDING_LIST_CACHE = _TTLStore(ttl=25.0, max_entries=256)
CONFIRMED_LIST_CACHE = _TTLStore(ttl=25.0, max_entries=128)
BADGE_COUNT_CACHE = _TTLStore(ttl=25.0, max_entries=256)


def auth_token_cache_key(authorization: str) -> str:
    raw = (authorization or "").strip().encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def get_auth_partner_hints(authorization: str) -> dict[int, dict[str, str | None]] | None:
    return AUTH_USERS_HINTS_CACHE.get(auth_token_cache_key(authorization))


def set_auth_partner_hints(
    authorization: str,
    value: dict[int, dict[str, str | None]],
) -> None:
    AUTH_USERS_HINTS_CACHE.set(auth_token_cache_key(authorization), value)


def partners_projects_cache_key(project_ids: list[str], authorization: str | None) -> str:
    pids = ",".join(sorted({str(p).strip() for p in project_ids if str(p).strip()}))
    tok = auth_token_cache_key(authorization or "") if authorization else "anon"
    return hashlib.sha256(f"{tok}|{pids}".encode()).hexdigest()[:40]


def get_partners_by_projects(project_ids: list[str], authorization: str | None) -> dict[str, list[int]] | None:
    return PARTNERS_BY_PROJECT_CACHE.get(partners_projects_cache_key(project_ids, authorization))


def set_partners_by_projects(
    project_ids: list[str],
    authorization: str | None,
    value: dict[str, list[int]],
) -> None:
    PARTNERS_BY_PROJECT_CACHE.set(partners_projects_cache_key(project_ids, authorization), value)


_RECONCILE_LAST_MONO: float = 0.0
_RECONCILE_MIN_INTERVAL_SEC = 60.0


def should_run_pending_reconcile(*, force: bool = False) -> bool:
    global _RECONCILE_LAST_MONO
    if force:
        _RECONCILE_LAST_MONO = time.monotonic()
        return True
    now = time.monotonic()
    if now - _RECONCILE_LAST_MONO < _RECONCILE_MIN_INTERVAL_SEC:
        return False
    _RECONCILE_LAST_MONO = now
    return True


def invalidate_partner_confirmation_read_caches() -> None:
    """Call after submit/confirm/delete/priority — does not touch DB rows."""
    PENDING_LIST_CACHE.clear()
    CONFIRMED_LIST_CACHE.clear()
    BADGE_COUNT_CACHE.clear()
    PARTNERS_BY_PROJECT_CACHE.clear()
