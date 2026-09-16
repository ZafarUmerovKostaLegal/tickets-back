from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import and_, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import AttendanceCameraEventModel

# Office timezone for day bounds (matches Hikvision event offsets we ingest).
_OFFICE_TZ = timezone(timedelta(hours=5))


async def ensure_camera_events_indexes(session: AsyncSession) -> None:
    """Idempotent uniqueness for older DBs created before UniqueConstraint."""
    await session.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_camera_event "
            "ON attendance_camera_events (camera_ip, person_id, event_time, checkpoint)"
        )
    )
    await session.commit()


async def upsert_camera_events(
    session: AsyncSession,
    rows: Iterable[dict[str, Any]],
) -> int:
    """Insert or update camera events. Returns number of unique rows attempted."""
    # Deduplicate within the batch — Postgres rejects ON CONFLICT when the same
    # conflict target appears twice in one INSERT.
    by_key: dict[tuple[str, str, Any, str], dict[str, Any]] = {}
    for r in rows:
        camera_ip = (r.get("camera_ip") or "").strip()
        person_id = (r.get("person_id") or "").strip()
        event_time = r.get("event_time")
        checkpoint = (r.get("checkpoint") or "Door").strip() or "Door"
        if not camera_ip or not person_id or event_time is None:
            continue
        key = (camera_ip, person_id, event_time, checkpoint)
        by_key[key] = {
            **r,
            "camera_ip": camera_ip,
            "person_id": person_id,
            "checkpoint": checkpoint,
        }
    payload = list(by_key.values())
    if not payload:
        return 0

    total = 0
    batch_size = 500
    for i in range(0, len(payload), batch_size):
        chunk = payload[i : i + batch_size]
        stmt = insert(AttendanceCameraEventModel).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["camera_ip", "person_id", "event_time", "checkpoint"],
            set_={
                "name": stmt.excluded.name,
                "department": stmt.excluded.department,
                "attendance_status": stmt.excluded.attendance_status,
                "door_no": stmt.excluded.door_no,
                "label": stmt.excluded.label,
                "ingested_at": datetime.utcnow(),
            },
        )
        await session.execute(stmt)
        total += len(chunk)
    await session.commit()
    return total


async def count_camera_events(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(AttendanceCameraEventModel))
    return int(result.scalar_one() or 0)


async def camera_events_time_bounds(session: AsyncSession) -> tuple[datetime | None, datetime | None]:
    result = await session.execute(
        select(
            func.min(AttendanceCameraEventModel.event_time),
            func.max(AttendanceCameraEventModel.event_time),
        )
    )
    row = result.one()
    return row[0], row[1]


def _day_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    start = datetime.combine(date_from, time.min, tzinfo=_OFFICE_TZ)
    end_exclusive = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=_OFFICE_TZ)
    return start, end_exclusive


async def list_camera_events_grouped_by_device(
    session: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    camera_ips: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return Hikvision-shaped device batches from DB for report builders."""
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    start, end_exclusive = _day_bounds(date_from, date_to)
    filters = [
        AttendanceCameraEventModel.event_time >= start,
        AttendanceCameraEventModel.event_time < end_exclusive,
    ]
    allowed = [ip.strip() for ip in (camera_ips or []) if ip and ip.strip()]
    if allowed:
        filters.append(AttendanceCameraEventModel.camera_ip.in_(allowed))

    result = await session.execute(
        select(AttendanceCameraEventModel)
        .where(and_(*filters))
        .order_by(AttendanceCameraEventModel.event_time.asc())
    )
    rows = list(result.scalars().all())
    by_ip: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        event_local = row.event_time.astimezone(_OFFICE_TZ) if row.event_time else None
        rec = {
            "person_id": row.person_id,
            "name": row.name or "-",
            "department": row.department or "-",
            "time": event_local.isoformat() if event_local else None,
            "checkpoint": row.checkpoint or "Door",
            "attendance_status": row.attendance_status or "-",
            "door_no": row.door_no,
            "label": row.label or "",
        }
        by_ip.setdefault(row.camera_ip, []).append(rec)
    return [{"camera_ip": ip, "records": recs, "error": None} for ip, recs in by_ip.items()]


async def list_known_camera_people(
    session: AsyncSession,
    *,
    since: date | None = None,
    camera_ips: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Distinct camera people seen in stored events (for roster without live /users)."""
    filters = []
    if since is not None:
        start, _ = _day_bounds(since, since)
        filters.append(AttendanceCameraEventModel.event_time >= start)
    allowed = [ip.strip() for ip in (camera_ips or []) if ip and ip.strip()]
    if allowed:
        filters.append(AttendanceCameraEventModel.camera_ip.in_(allowed))

    stmt = (
        select(
            AttendanceCameraEventModel.person_id,
            func.max(AttendanceCameraEventModel.name).label("name"),
            func.max(AttendanceCameraEventModel.department).label("department"),
        )
        .where(and_(*filters) if filters else True)
        .group_by(AttendanceCameraEventModel.person_id)
        .order_by(AttendanceCameraEventModel.person_id)
    )
    result = await session.execute(stmt)
    return [
        {
            "person_id": row.person_id,
            "name": row.name,
            "department": row.department,
        }
        for row in result.all()
        if (row.person_id or "").strip()
    ]
