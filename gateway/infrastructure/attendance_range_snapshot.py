"""In-memory снимок маркеров посещаемости для быстрого /report/range."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Optional

from application.attendance_range_builder import fetch_attendance_range_items_for_year_to_date
from infrastructure.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class AttendanceRangeSnapshotState:
    items: list[dict] = field(default_factory=list)
    coverage_start: Optional[date] = None
    coverage_end: Optional[date] = None
    year: Optional[int] = None
    built_at: Optional[datetime] = None
    building: bool = False
    last_error: Optional[str] = None


_state = AttendanceRangeSnapshotState()
_lock = asyncio.Lock()
_scheduler_task: Optional[asyncio.Task] = None
_refresh_scheduled = False


def get_snapshot_state() -> AttendanceRangeSnapshotState:
    return _state


def _snapshot_meta(*, stale: bool, status: str) -> dict[str, Any]:
    built_at = _state.built_at.isoformat() if _state.built_at else None
    settings = get_settings()
    return {
        "status": status,
        "built_at": built_at,
        "stale": stale,
        "refresh_interval_sec": settings.attendance_range_snapshot_refresh_sec,
        "item_count": len(_state.items),
        "coverage_start": _state.coverage_start.isoformat() if _state.coverage_start else None,
        "coverage_end": _state.coverage_end.isoformat() if _state.coverage_end else None,
    }


def _is_stale(now: datetime) -> bool:
    if _state.built_at is None:
        return True
    settings = get_settings()
    age = (now - _state.built_at).total_seconds()
    return age >= settings.attendance_range_snapshot_refresh_sec


def filter_snapshot_items(start: date, end: date) -> list[dict]:
    start_s = start.isoformat()
    end_s = end.isoformat()
    return [
        item
        for item in _state.items
        if start_s <= (item.get("date") or "") <= end_s
    ]


def try_get_snapshot_response(start: date, end: date) -> Optional[dict[str, Any]]:
    settings = get_settings()
    if not settings.attendance_range_snapshot_enabled:
        return None

    now = datetime.now(timezone.utc)
    today = date.today()
    year = today.year

    if start.year != year or end.year != year:
        return None
    if end > today:
        return None

    if _state.building and not _state.items:
        return {
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "items": [],
            "snapshot": _snapshot_meta(stale=True, status="building"),
        }

    if _state.year != year or not _state.items:
        schedule_refresh()
        return {
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "items": [],
            "snapshot": _snapshot_meta(stale=True, status="empty"),
        }

    stale = _is_stale(now)
    if stale:
        schedule_refresh()

    return {
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "items": filter_snapshot_items(start, end),
        "snapshot": _snapshot_meta(stale=stale, status="ready"),
    }


def schedule_refresh() -> None:
    global _refresh_scheduled
    if _state.building or _refresh_scheduled:
        return
    _refresh_scheduled = True
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(refresh_snapshot())
    except RuntimeError:
        pass


async def refresh_snapshot(*, year: Optional[int] = None) -> None:
    global _refresh_scheduled
    settings = get_settings()
    if not settings.attendance_range_snapshot_enabled:
        _refresh_scheduled = False
        return

    if _lock.locked():
        _refresh_scheduled = False
        return

    async with _lock:
        _refresh_scheduled = False
        if _state.building:
            return
        _state.building = True
        _state.last_error = None

        target_year = year or date.today().year
        try:
            items = await fetch_attendance_range_items_for_year_to_date(target_year)
            today = date.today()
            coverage_end = today if today.year == target_year else date(target_year, 12, 31)
            _state.items = items
            _state.year = target_year
            _state.coverage_start = date(target_year, 1, 1)
            _state.coverage_end = coverage_end
            _state.built_at = datetime.now(timezone.utc)
            logger.info(
                "attendance range snapshot built year=%s items=%s coverage=%s..%s",
                target_year,
                len(items),
                _state.coverage_start,
                _state.coverage_end,
            )
        except Exception as exc:
            _state.last_error = str(exc)
            logger.exception("attendance range snapshot refresh failed")
        finally:
            _state.building = False


async def _scheduler_loop() -> None:
    settings = get_settings()
    if not settings.attendance_range_snapshot_enabled:
        return
    await refresh_snapshot()
    while True:
        await asyncio.sleep(max(30, settings.attendance_range_snapshot_refresh_sec))
        await refresh_snapshot()


async def start_snapshot_scheduler() -> None:
    global _scheduler_task
    settings = get_settings()
    if not settings.attendance_range_snapshot_enabled:
        return
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop())


async def stop_snapshot_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is None:
        return
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except asyncio.CancelledError:
        pass
    _scheduler_task = None
