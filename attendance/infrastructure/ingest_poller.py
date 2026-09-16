from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from application.ingest_camera_events import ingest_date_range
from infrastructure.config import get_settings

logger = logging.getLogger(__name__)

_scheduler_task: asyncio.Task | None = None
_backfill_task: asyncio.Task | None = None
_state: dict = {
    "poller_running": False,
    "last_poll_at": None,
    "last_poll_error": None,
    "last_poll_upserted": 0,
    "backfill_running": False,
    "backfill_started_at": None,
    "backfill_finished_at": None,
    "backfill_result": None,
    "backfill_error": None,
}


def ingest_status() -> dict:
    return dict(_state)


async def _poll_once() -> None:
    settings = get_settings()
    lookback = max(5, int(settings.attendance_ingest_lookback_minutes))
    # Overlap window so we don't miss events around poll boundaries.
    end = date.today()
    start = (datetime.now(timezone.utc) - timedelta(minutes=lookback)).date()
    if start > end:
        start = end
    result = await ingest_date_range(
        start,
        end,
        max_records_per_device=max(500, int(settings.attendance_ingest_max_records_per_device)),
        pause_sec=0.05,
    )
    _state["last_poll_at"] = datetime.utcnow().isoformat() + "Z"
    _state["last_poll_upserted"] = result.upserted
    _state["last_poll_error"] = "; ".join(result.device_errors[:5]) if result.device_errors else None
    logger.info(
        "camera ingest poll %s..%s upserted=%s errors=%s",
        result.date_from,
        result.date_to,
        result.upserted,
        len(result.device_errors),
    )


async def _scheduler_loop() -> None:
    settings = get_settings()
    if not settings.attendance_ingest_enabled:
        return
    _state["poller_running"] = True
    # First poll shortly after boot (give DB/schema a moment).
    await asyncio.sleep(5)
    while True:
        try:
            await _poll_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _state["last_poll_error"] = str(exc)
            logger.exception("camera ingest poll failed")
        interval = max(30, int(settings.attendance_ingest_interval_sec))
        await asyncio.sleep(interval)


async def start_ingest_poller() -> None:
    global _scheduler_task
    settings = get_settings()
    if not settings.attendance_ingest_enabled:
        logger.info("camera ingest poller disabled (ATTENDANCE_INGEST_ENABLED=false)")
        return
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop(), name="attendance-camera-ingest")
    logger.info(
        "camera ingest poller started (interval=%ss lookback=%smin)",
        settings.attendance_ingest_interval_sec,
        settings.attendance_ingest_lookback_minutes,
    )


async def stop_ingest_poller() -> None:
    global _scheduler_task
    _state["poller_running"] = False
    if _scheduler_task is None:
        return
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except asyncio.CancelledError:
        pass
    _scheduler_task = None


async def run_backfill_job(date_from: date, date_to: date) -> dict:
    if _state.get("backfill_running"):
        raise RuntimeError("Backfill already running")
    _state["backfill_running"] = True
    _state["backfill_started_at"] = datetime.utcnow().isoformat() + "Z"
    _state["backfill_finished_at"] = None
    _state["backfill_result"] = None
    _state["backfill_error"] = None
    try:
        settings = get_settings()
        result = await ingest_date_range(
            date_from,
            date_to,
            max_records_per_device=max(500, int(settings.attendance_ingest_max_records_per_device)),
            pause_sec=0.2,
        )
        payload = {
            "date_from": result.date_from,
            "date_to": result.date_to,
            "days": result.days,
            "fetched": result.fetched,
            "upserted": result.upserted,
            "device_errors": result.device_errors[:50],
            "cancelled": result.cancelled,
        }
        _state["backfill_result"] = payload
        return payload
    except Exception as exc:
        _state["backfill_error"] = str(exc)
        logger.exception("camera events backfill failed")
        raise
    finally:
        _state["backfill_running"] = False
        _state["backfill_finished_at"] = datetime.utcnow().isoformat() + "Z"


def start_backfill_background(date_from: date, date_to: date) -> None:
    global _backfill_task
    if _state.get("backfill_running"):
        raise RuntimeError("Backfill already running")
    if _backfill_task is not None and not _backfill_task.done():
        raise RuntimeError("Backfill already running")

    async def _runner() -> None:
        try:
            await run_backfill_job(date_from, date_to)
        except Exception:
            pass

    _backfill_task = asyncio.create_task(_runner(), name="attendance-camera-backfill")
