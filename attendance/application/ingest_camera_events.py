from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from infrastructure.camera_events_repo import upsert_camera_events
from infrastructure.config import get_settings
from infrastructure.database import async_session_factory
from infrastructure.hikvision_client import get_attendance_from_devices
from infrastructure.hikvision_hosts import configured_hikvision_hosts

logger = logging.getLogger(__name__)


def parse_event_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    # Hikvision sometimes returns "2026-01-15T08:30:00" without offset
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(s[:19], fmt)
                break
            except ValueError:
                dt = None
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _record_to_row(camera_ip: str, rec: dict[str, Any]) -> dict[str, Any] | None:
    person_id = (rec.get("person_id") or "").strip()
    if not person_id or person_id == "-":
        return None
    event_time = parse_event_time(rec.get("time"))
    if event_time is None:
        return None
    checkpoint = (rec.get("checkpoint") or "Door").strip() or "Door"
    name = rec.get("name")
    department = rec.get("department")
    status = rec.get("attendance_status")
    label = rec.get("label")
    return {
        "camera_ip": camera_ip.strip(),
        "person_id": person_id,
        "event_time": event_time,
        "name": None if not name or name == "-" else str(name)[:256],
        "department": None if not department or department == "-" else str(department)[:256],
        "checkpoint": checkpoint[:256],
        "attendance_status": None if not status or status == "-" else str(status)[:64],
        "door_no": rec.get("door_no") if isinstance(rec.get("door_no"), int) else None,
        "label": None if not label else str(label)[:256],
        "ingested_at": datetime.utcnow(),
    }


@dataclass
class IngestDayResult:
    day: str
    fetched: int = 0
    upserted: int = 0
    device_errors: list[str] = field(default_factory=list)


@dataclass
class IngestRangeResult:
    date_from: str
    date_to: str
    days: int = 0
    fetched: int = 0
    upserted: int = 0
    device_errors: list[str] = field(default_factory=list)
    cancelled: bool = False


ProgressCb = Optional[Callable[[IngestDayResult], None]]


async def ingest_one_day(
    day: date,
    *,
    max_records_per_device: int = 10000,
    progress: ProgressCb = None,
) -> IngestDayResult:
    settings = get_settings()
    hosts = configured_hikvision_hosts(settings)
    result = IngestDayResult(day=day.isoformat())
    if not hosts:
        result.device_errors.append("Hikvision hosts are not configured")
        if progress:
            progress(result)
        return result

    device_results = await asyncio.to_thread(
        get_attendance_from_devices,
        hosts,
        settings.hikvision_device_port,
        settings.hikvision_device_user,
        settings.hikvision_device_password,
        day,
        day,
        max_records_per_device,
        settings.hikvision_request_timeout,
    )

    rows: list[dict[str, Any]] = []
    for dev in device_results:
        camera_ip = str(dev.get("camera_ip") or "")
        err = dev.get("error")
        if err:
            result.device_errors.append(f"{camera_ip}: {err}")
        records = dev.get("records") or []
        result.fetched += len(records)
        for rec in records:
            if not isinstance(rec, dict):
                continue
            row = _record_to_row(camera_ip, rec)
            if row:
                rows.append(row)

    if rows:
        async with async_session_factory() as session:
            result.upserted = await upsert_camera_events(session, rows)

    if progress:
        progress(result)
    return result


async def ingest_date_range(
    date_from: date,
    date_to: date,
    *,
    max_records_per_device: int = 10000,
    pause_sec: float = 0.15,
    should_cancel: Optional[Callable[[], bool]] = None,
    progress: ProgressCb = None,
) -> IngestRangeResult:
    if date_to < date_from:
        date_from, date_to = date_to, date_from

    out = IngestRangeResult(date_from=date_from.isoformat(), date_to=date_to.isoformat())
    day = date_from
    while day <= date_to:
        if should_cancel and should_cancel():
            out.cancelled = True
            break
        day_res = await ingest_one_day(
            day,
            max_records_per_device=max_records_per_device,
            progress=progress,
        )
        out.days += 1
        out.fetched += day_res.fetched
        out.upserted += day_res.upserted
        out.device_errors.extend(day_res.device_errors)
        logger.info(
            "camera ingest %s: fetched=%s upserted=%s errors=%s",
            day_res.day,
            day_res.fetched,
            day_res.upserted,
            len(day_res.device_errors),
        )
        day += timedelta(days=1)
        if pause_sec > 0 and day <= date_to:
            await asyncio.sleep(pause_sec)
    return out
