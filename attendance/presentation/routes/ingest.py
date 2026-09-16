from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.camera_events_repo import (
    camera_events_time_bounds,
    count_camera_events,
    list_camera_events_grouped_by_device,
    list_known_camera_people,
)
from infrastructure.database import get_session
from infrastructure.ingest_poller import ingest_status, start_backfill_background

router = APIRouter(prefix="/hikvision/ingest", tags=["hikvision-ingest"])


def _parse_day(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}, expected YYYY-MM-DD") from exc


@router.get("/status")
async def get_ingest_status(session: AsyncSession = Depends(get_session)):
    total = await count_camera_events(session)
    mn, mx = await camera_events_time_bounds(session)
    runtime = ingest_status()
    return {
        "events_total": total,
        "min_event_time": mn.isoformat() if mn else None,
        "max_event_time": mx.isoformat() if mx else None,
        **runtime,
    }


@router.get("/events")
async def get_stored_camera_events(
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to: str = Query(..., description="YYYY-MM-DD"),
    camera_ip: Optional[str] = Query(None, description="Optional comma-separated camera IPs"),
    person_id: Optional[str] = Query(None, description="Optional camera employee no"),
    session: AsyncSession = Depends(get_session),
):
    """Fast path: AcsEvent history from Postgres (same shape as live /hikvision/attendance)."""
    start = _parse_day(date_from, "date_from")
    end = _parse_day(date_to, "date_to")
    ips = [p.strip() for p in (camera_ip or "").split(",") if p.strip()] or None
    devices = await list_camera_events_grouped_by_device(
        session,
        date_from=start,
        date_to=end,
        camera_ips=ips,
    )
    pid = (person_id or "").strip()
    if not pid:
        return devices
    filtered = []
    for dev in devices:
        recs = [r for r in (dev.get("records") or []) if (r.get("person_id") or "").strip() == pid]
        if recs:
            filtered.append({**dev, "records": recs})
    return filtered


@router.get("/people")
async def get_known_camera_people(
    since: Optional[str] = Query(None, description="YYYY-MM-DD — only people seen since this day"),
    camera_ip: Optional[str] = Query(None, description="Optional comma-separated camera IPs"),
    session: AsyncSession = Depends(get_session),
):
    """Distinct people from stored camera events (fast roster without live device scrape)."""
    since_day = _parse_day(since, "since") if since else None
    ips = [p.strip() for p in (camera_ip or "").split(",") if p.strip()] or None
    return await list_known_camera_people(session, since=since_day, camera_ips=ips)


@router.post("/backfill")
async def trigger_backfill(
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    year: Optional[int] = Query(None, description="Shortcut: full calendar year (e.g. 2026)"),
):
    """Start background backfill of camera events into attendance_camera_events."""
    today = date.today()
    if year is not None:
        start = date(year, 1, 1)
        end = date(year, 12, 31)
    else:
        if not date_from or not date_to:
            raise HTTPException(status_code=400, detail="Provide year=2026 or both date_from and date_to")
        start = _parse_day(date_from, "date_from")
        end = _parse_day(date_to, "date_to")

    if end > today:
        end = today
    if end < start:
        raise HTTPException(status_code=400, detail="date_to must be >= date_from")

    try:
        start_backfill_background(start, end)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "started": True,
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "message": "Backfill started in background. Poll GET /hikvision/ingest/status",
    }
