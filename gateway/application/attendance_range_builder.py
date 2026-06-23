"""Сборка compact-маркеров посещаемости (late/absent) для графика отпусков."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from typing import Optional

import httpx
from fastapi import HTTPException

from infrastructure.config import get_settings

RANGE_REPORT_HTTP_TIMEOUT_SEC = 300.0


def allowed_camera_ips() -> list[str]:
    s = get_settings()
    allowed = (s.attendance_hikvision_allowed_ips or "").strip()
    return [h.strip() for h in allowed.split(",") if h.strip()]


def iter_dates_inclusive(start: date, end: date) -> list[date]:
    days = (end - start).days
    if days < 0:
        raise ValueError("date_from must be before or equal to date_to")
    return [start + timedelta(days=i) for i in range(days + 1)]


def month_chunks_inclusive(start: date, end: date) -> list[tuple[date, date]]:
    if start > end:
        return []
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        month_start = date(cursor.year, cursor.month, 1)
        if cursor.month == 12:
            month_end = date(cursor.year, 12, 31)
        else:
            month_end = date(cursor.year, cursor.month + 1, 1) - timedelta(days=1)
        chunk_from = cursor if cursor > month_start else month_start
        chunk_to = end if end < month_end else month_end
        chunks.append((chunk_from, chunk_to))
        cursor = date(chunk_to.year, chunk_to.month, chunk_to.day) + timedelta(days=1)
        if chunk_to >= end:
            break
    return chunks


def _parse_event_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _late_border_dt(report_day: date, workday: dict) -> datetime:
    start_val = time.fromisoformat(workday.get("workday_start", "09:00:00"))
    late_threshold = int(workday.get("late_threshold_minutes", 0) or 0)
    return datetime.combine(report_day, start_val) + timedelta(minutes=late_threshold)


def build_range_roster_from_mappings(mappings: list) -> dict[str, dict]:
    roster_by_employee_no: dict[str, dict] = {}
    for mapping in mappings:
        employee_no = (mapping.get("camera_employee_no") or "").strip()
        if not employee_no:
            continue
        roster_by_employee_no[employee_no] = {
            "camera_employee_no": employee_no,
            "camera_name": mapping.get("camera_name"),
            "department": None,
            "camera_ips": set(),
        }
    return roster_by_employee_no


def build_hikvision_roster(hikvision_users_devices: list) -> dict[str, dict]:
    """Тот же состав сотрудников, что в GET /report/daily."""
    roster_by_employee_no: dict[str, dict] = {}
    for dev in hikvision_users_devices:
        camera_ip = dev.get("camera_ip")
        for hu in (dev.get("users") or []):
            employee_no = (hu.get("employee_no") or "").strip()
            if not employee_no:
                continue
            if employee_no not in roster_by_employee_no:
                roster_by_employee_no[employee_no] = {
                    "camera_employee_no": employee_no,
                    "camera_name": hu.get("name"),
                    "department": hu.get("department"),
                    "camera_ips": set(),
                }
            roster_by_employee_no[employee_no]["camera_ips"].add(camera_ip)
            if not roster_by_employee_no[employee_no].get("camera_name") and hu.get("name"):
                roster_by_employee_no[employee_no]["camera_name"] = hu.get("name")
            if not roster_by_employee_no[employee_no].get("department") and hu.get("department"):
                roster_by_employee_no[employee_no]["department"] = hu.get("department")
    return roster_by_employee_no


def compute_employee_day_status(
    report_day: date,
    employee_no: str,
    *,
    workday: dict,
    first_events_for_day: dict[str, dict],
) -> tuple[str, Optional[str]]:
    late_border = _late_border_dt(report_day, workday)
    first = first_events_for_day.get(employee_no)
    if not first:
        return "absent", None
    first_dt = first["dt"]
    first_time = first_dt.isoformat()
    status = "late" if first_dt.replace(tzinfo=None) > late_border else "present_on_time"
    return status, first_time


def build_daily_report_items(
    report_day: date,
    *,
    workday: dict,
    roster_by_employee_no: dict[str, dict],
    mapping_by_employee_no: dict[str, dict],
    app_users_by_id: dict,
    first_events_for_day: dict[str, dict],
    explanations_for_day: list,
) -> tuple[list[dict], dict[str, int], datetime]:
    """Полный дневной отчёт — та же логика, что GET /report/daily."""
    late_border_dt = _late_border_dt(report_day, workday)
    explanation_by_key = {
        f"{(x.get('camera_employee_no') or '').strip()}|{(x.get('status') or '').strip().lower()}": x
        for x in explanations_for_day
        if (x.get("camera_employee_no") or "").strip()
    }
    items: list[dict] = []
    counts = {"present_on_time": 0, "late": 0, "absent": 0}
    for employee_no, user in roster_by_employee_no.items():
        mapping = mapping_by_employee_no.get(employee_no)
        uid = mapping.get("app_user_id") if mapping else None
        app_user = app_users_by_id.get(uid) if uid is not None else None
        display_name = (
            (app_user or {}).get("display_name")
            or (app_user or {}).get("email")
            or user.get("camera_name")
            or f"Hikvision #{employee_no}"
        )
        status, first_time = compute_employee_day_status(
            report_day,
            employee_no,
            workday=workday,
            first_events_for_day=first_events_for_day,
        )
        counts[status] += 1
        explanation = explanation_by_key.get(f"{employee_no}|{status}")
        explanation_file_path = (explanation or {}).get("explanation_file_path")
        explanation_file_url = (
            f"/api/v1/media/{explanation_file_path}" if explanation_file_path else None
        )
        items.append(
            {
                "app_user_id": uid,
                "display_name": display_name,
                "email": (app_user or {}).get("email"),
                "role": (app_user or {}).get("role"),
                "is_mapped": uid is not None,
                "camera_employee_no": employee_no,
                "camera_name": user.get("camera_name"),
                "camera_ips": sorted([ip for ip in user.get("camera_ips", set()) if ip]),
                "department": user.get("department"),
                "status": status,
                "first_event_time": first_time,
                "explanation_text": (explanation or {}).get("explanation_text"),
                "explanation_file_path": explanation_file_path,
                "explanation_file_url": explanation_file_url,
                "explanation_updated_at": (explanation or {}).get("updated_at"),
            }
        )
    return items, counts, late_border_dt


def _merge_attendance_device_batches(by_ip: dict, batch: list) -> dict:
    for dev in batch:
        ip = dev.get("camera_ip")
        records = list(dev.get("records") or [])
        if ip in by_ip:
            by_ip[ip]["records"] = (by_ip[ip].get("records") or []) + records
        else:
            by_ip[ip] = {"camera_ip": ip, "records": records}
    return by_ip


async def fetch_attendance_events_for_range(
    client: httpx.AsyncClient,
    base: str,
    allowed: list[str],
    start: date,
    end: date,
    *,
    chunk_days: int = 31,
    max_records_per_device: int = 5000,
) -> list:
    by_ip: dict = {}
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=chunk_days - 1), end)
        params = {
            "date_from": chunk_start.isoformat(),
            "date_to": chunk_end.isoformat(),
            "max_records_per_device": max_records_per_device,
        }
        if allowed:
            params["camera_ip"] = ",".join(allowed)
        r = await client.get(f"{base}/hikvision/attendance", params=params)
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code, detail=r.text or "Attendance service error")
        _merge_attendance_device_batches(by_ip, r.json() or [])
        chunk_start = chunk_end + timedelta(days=1)
    return list(by_ip.values())


def index_first_events_by_day(
    events_devices: list,
    *,
    start: date,
    end: date,
) -> dict[str, dict[str, dict]]:
    start_s = start.isoformat()
    end_s = end.isoformat()
    result: dict[str, dict[str, dict]] = {}
    for dev in events_devices:
        for rec in (dev.get("records") or []):
            employee_no = (rec.get("person_id") or "").strip()
            if not employee_no:
                continue
            dt = _parse_event_dt(rec.get("time"))
            if not dt:
                continue
            day_key = dt.date().isoformat()
            if day_key < start_s or day_key > end_s:
                continue
            day_bucket = result.setdefault(day_key, {})
            prev = day_bucket.get(employee_no)
            if not prev or dt < prev["dt"]:
                day_bucket[employee_no] = {"dt": dt, "record": rec}
    return result


def build_range_report_items(
    *,
    start: date,
    end: date,
    workday: dict,
    roster_by_employee_no: dict[str, dict],
    mapping_by_employee_no: dict[str, dict],
    app_users_by_id: dict,
    first_events_by_day: dict[str, dict[str, dict]],
    explanations: list,
) -> list[dict]:
    start_s = start.isoformat()
    end_s = end.isoformat()
    explanation_by_key: dict[str, dict] = {}
    for x in explanations:
        day_str = (x.get("day") or "").strip()
        employee_no = (x.get("camera_employee_no") or "").strip()
        status = (x.get("status") or "").strip().lower()
        if not day_str or not employee_no or status not in {"late", "absent"}:
            continue
        if day_str < start_s or day_str > end_s:
            continue
        explanation_by_key[f"{day_str}|{employee_no}|{status}"] = x

    items: list[dict] = []
    for day in iter_dates_inclusive(start, end):
        day_str = day.isoformat()
        first_by_emp = first_events_by_day.get(day_str, {})

        for employee_no, user in roster_by_employee_no.items():
            mapping = mapping_by_employee_no.get(employee_no)
            uid = mapping.get("app_user_id") if mapping else None
            if uid is None:
                continue

            app_user = app_users_by_id.get(uid) or {}
            display_name = (
                app_user.get("display_name")
                or app_user.get("email")
                or user.get("camera_name")
                or f"Hikvision #{employee_no}"
            )

            status, first_time = compute_employee_day_status(
                day,
                employee_no,
                workday=workday,
                first_events_for_day=first_by_emp,
            )
            if status not in {"late", "absent"}:
                continue

            explanation = explanation_by_key.get(f"{day_str}|{employee_no}|{status}")
            explanation_file_path = (explanation or {}).get("explanation_file_path")
            explanation_file_url = (
                f"/api/v1/media/{explanation_file_path}" if explanation_file_path else None
            )
            items.append(
                {
                    "date": day_str,
                    "app_user_id": uid,
                    "status": status,
                    "first_event_time": first_time,
                    "camera_employee_no": employee_no,
                    "display_name": display_name,
                    "explanation_text": (explanation or {}).get("explanation_text"),
                    "explanation_file_url": explanation_file_url,
                }
            )
    return items


async def fetch_attendance_range_items(
    start: date,
    end: date,
    *,
    auth_headers: dict[str, str] | None = None,
) -> list[dict]:
    settings = get_settings()
    base = (settings.attendance_service_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="Attendance service not configured")

    allowed = allowed_camera_ips()
    explanation_params = {
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
    }
    hikvision_users_params = {"max_users_per_device": 20000}
    if allowed:
        hikvision_users_params["camera_ip"] = ",".join(allowed)

    try:
        async with httpx.AsyncClient(timeout=RANGE_REPORT_HTTP_TIMEOUT_SEC) as client:
            workday_r, mappings_r, explanations_r, events_devices, hikvision_users_r, users_r = await asyncio.gather(
                client.get(f"{base}/settings/workday"),
                client.get(f"{base}/hikvision/mappings"),
                client.get(f"{base}/hikvision/explanations", params=explanation_params),
                fetch_attendance_events_for_range(client, base, allowed, start, end),
                client.get(f"{base}/hikvision/users", params=hikvision_users_params),
                client.get(
                    f"{settings.auth_service_url}/users",
                    params={"include_archived": False},
                    headers=auth_headers or None,
                ),
            )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Attendance service unavailable")

    if workday_r.status_code >= 400:
        raise HTTPException(status_code=workday_r.status_code, detail=workday_r.text or "Attendance service error")
    if mappings_r.status_code >= 400:
        raise HTTPException(status_code=mappings_r.status_code, detail=mappings_r.text or "Attendance service error")
    if explanations_r.status_code >= 400:
        raise HTTPException(status_code=explanations_r.status_code, detail=explanations_r.text or "Attendance service error")
    if hikvision_users_r.status_code >= 400:
        raise HTTPException(
            status_code=hikvision_users_r.status_code,
            detail=hikvision_users_r.text or "Attendance service error",
        )

    workday = workday_r.json() or {}
    mappings = mappings_r.json() or []
    explanations = explanations_r.json() or []
    app_users = users_r.json() or [] if users_r.status_code < 400 else []
    app_users_by_id = {u.get("id"): u for u in app_users if u.get("id") is not None}
    mapping_by_employee_no = {
        (m.get("camera_employee_no") or "").strip(): m
        for m in mappings
        if (m.get("camera_employee_no") or "").strip()
    }
    roster_by_employee_no = build_hikvision_roster(hikvision_users_r.json() or [])
    first_events_by_day = index_first_events_by_day(events_devices, start=start, end=end)
    return build_range_report_items(
        start=start,
        end=end,
        workday=workday,
        roster_by_employee_no=roster_by_employee_no,
        mapping_by_employee_no=mapping_by_employee_no,
        app_users_by_id=app_users_by_id,
        first_events_by_day=first_events_by_day,
        explanations=explanations,
    )


async def fetch_attendance_range_items_for_year_to_date(
    year: int,
    *,
    auth_headers: dict[str, str] | None = None,
) -> list[dict]:
    today = date.today()
    end = today if today.year == year else date(year, 12, 31)
    start = date(year, 1, 1)
    if start > end:
        return []

    merged: list[dict] = []
    for chunk_start, chunk_end in month_chunks_inclusive(start, end):
        merged.extend(
            await fetch_attendance_range_items(
                chunk_start,
                chunk_end,
                auth_headers=auth_headers,
            )
        )
    return merged
