"""
Simple in-process TTL cache for expensive report computations.

Two separate caches:
- `DIM_CACHE`    — dimension maps (users/projects/clients/tasks). TTL 60 s.
  Invalidated explicitly when objects are created/updated/deleted.
- `REPORT_CACHE` — full report responses keyed by query parameters. TTL 90 s.
  Evicted automatically on TTL expiry; also invalidated when
  time entries change (created / deleted / voided) or rate changes occur.

Thread-safety: asyncio single-event-loop model — no locking needed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

_log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#  Generic TTL store
# --------------------------------------------------------------------------- #

class _TTLStore:
    """Key → (value, expiry_ts) mapping with automatic eviction on get."""

    def __init__(self, name: str, ttl: float) -> None:
        self._name = name
        self._ttl = ttl
        self._data: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, exp = entry
        if time.monotonic() > exp:
            del self._data[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._data[key] = (value, time.monotonic() + self._ttl)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        n = len(self._data)
        self._data.clear()
        if n:
            _log.debug("report_cache: cleared %s entries from %s", n, self._name)

    def evict_expired(self) -> int:
        now = time.monotonic()
        expired = [k for k, (_, exp) in self._data.items() if now > exp]
        for k in expired:
            del self._data[k]
        return len(expired)

    def __len__(self) -> int:
        return len(self._data)


# --------------------------------------------------------------------------- #
#  Module-level singletons
# --------------------------------------------------------------------------- #

DIM_CACHE   = _TTLStore("dim",    ttl=60.0)   # users/projects/clients/tasks maps
REPORT_CACHE = _TTLStore("report", ttl=90.0)  # full report payloads


# --------------------------------------------------------------------------- #
#  Dimension cache helpers
# --------------------------------------------------------------------------- #

def get_dim(key: str) -> Any:
    return DIM_CACHE.get(key)


def set_dim(key: str, value: Any) -> None:
    DIM_CACHE.set(key, value)


def invalidate_dim(key: str) -> None:
    """Call when a project/client/task/user is created or updated."""
    DIM_CACHE.delete(key)


def invalidate_all_dims() -> None:
    DIM_CACHE.clear()


# --------------------------------------------------------------------------- #
#  Report cache helpers
# --------------------------------------------------------------------------- #

def _report_key(params: dict[str, Any]) -> str:
    """Stable hash over report parameters."""
    canonical = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha1(canonical.encode()).hexdigest()  # noqa: S324


def get_report(params: dict[str, Any]) -> Any:
    return REPORT_CACHE.get(_report_key(params))


def set_report(params: dict[str, Any], value: Any) -> None:
    REPORT_CACHE.set(_report_key(params), value)


def invalidate_all_reports() -> None:
    """Call when time entries or hourly rates change (reports recompute amounts from rates)."""
    REPORT_CACHE.clear()


def evict_expired_reports() -> int:
    return REPORT_CACHE.evict_expired()
