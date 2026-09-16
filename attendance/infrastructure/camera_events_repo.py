from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import AttendanceCameraEventModel


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
