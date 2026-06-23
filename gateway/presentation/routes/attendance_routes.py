

import asyncio
from datetime import date, datetime, time, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from infrastructure.auth_upstream import verify_bearer_and_get_user
from infrastructure.config import get_settings
from infrastructure.upstream_auth_context import merge_upstream_headers


class WorkdaySettingsUpdateBody(BaseModel):
    workday_start: Optional[time] = None
    workday_end: Optional[time] = None
    late_threshold_minutes: Optional[int] = None
    daily_hours_norm: Optional[int] = None


class HikvisionMappingUpsertBody(BaseModel):
    camera_employee_no: str
    app_user_id: int
    camera_name: Optional[str] = None


class AttendanceExplanationUpsertBody(BaseModel):
    day: str
    camera_employee_no: str
    status: str
    explanation_text: str
    app_user_id: Optional[int] = None

router = APIRouter(prefix="/api/v1/attendance", tags=["attendance"])
router_compat = APIRouter(tags=["attendance-compat"])

ROLES_CAN_VIEW = {
    "Главный администратор",
    "Администратор",
    "Партнер",
    "IT отдел",
    "Офис менеджер",
    "Офис-менеджер",
    "Сотрудник",
}


ROLES_CAN_UPDATE_WORKDAY_SETTINGS = {
    "Главный администратор",
    "Администратор",
    "Партнер",
    "IT отдел",
    "Офис менеджер",
    "Офис-менеджер",
}


ROLES_CAN_MANAGE_HIKVISION_MAPPINGS = {
    "Главный администратор",
    "Администратор",
    "Партнер",
    "Офис менеджер",
    "Офис-менеджер",
}


ROLES_CAN_VIEW_ATTENDANCE_RANGE = ROLES_CAN_MANAGE_HIKVISION_MAPPINGS


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    user = await verify_bearer_and_get_user(request, authorization)
    role = (user.get("role") or "").strip()
    if role not in ROLES_CAN_VIEW:
        raise HTTPException(
            status_code=403,
            detail="Only administrators, IT, office managers and employees can view attendance",
        )
    return user


def _require_attendance_range_access(user: dict) -> None:
    role = (user.get("role") or "").strip()
    if role not in ROLES_CAN_VIEW_ATTENDANCE_RANGE:
        raise HTTPException(
            status_code=403,
            detail="Only administrators, partners and office managers can view attendance markers in vacation",
        )


def _allowed_camera_ips() -> list[str]:

    s = get_settings()
    allowed = (s.attendance_hikvision_allowed_ips or "").strip()
    return [h.strip() for h in allowed.split(",") if h.strip()]


def _parse_iso_date(value: Optional[str]) -> date:
    if value and value.strip():
        return date.fromisoformat(value.strip())
    return date.today()


def _parse_required_iso_date(value: str, *, field_name: str) -> date:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field_name} must be YYYY-MM-DD") from None


def _iter_dates(start: date, end: date) -> list[date]:
    days = (end - start).days
    if days < 0:
        raise HTTPException(status_code=400, detail="date_from must be before or equal to date_to")
    if days > 370:
        raise HTTPException(status_code=400, detail="Date range is too large")
    return [start + timedelta(days=i) for i in range(days + 1)]


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


def _build_roster_by_employee_no(hikvision_users_devices: list) -> dict[str, dict]:
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


def _merge_attendance_device_batches(by_ip: dict, batch: list) -> dict:
    for dev in batch:
        ip = dev.get("camera_ip")
        records = list(dev.get("records") or [])
        if ip in by_ip:
            by_ip[ip]["records"] = (by_ip[ip].get("records") or []) + records
        else:
            by_ip[ip] = {"camera_ip": ip, "records": records}
    return by_ip


def _index_first_events_by_day(
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


def _build_range_report_items(
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
    for day in _iter_dates(start, end):
        day_str = day.isoformat()
        late_border = _late_border_dt(day, workday)
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

            first = first_by_emp.get(employee_no)
            if not first:
                status = "absent"
                first_time = None
            else:
                first_dt = first["dt"]
                first_time = first_dt.isoformat()
                status = "late" if first_dt.replace(tzinfo=None) > late_border else "present_on_time"

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


async def _fetch_attendance_events_for_range(
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


@router.get("/hikvision/attendance")
async def get_hikvision_attendance(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    max_records_per_device: int = Query(500, ge=1, le=5000),
    person_id: Optional[str] = Query(None),
    name: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    checkpoint: Optional[str] = Query(None),
    attendance_status: Optional[str] = Query(None),
    _: dict = Depends(get_current_user),
):

    settings = get_settings()
    base = (settings.attendance_service_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="Attendance service not configured")
    allowed = _allowed_camera_ips()
    params = {
        "date_from": date_from,
        "date_to": date_to,
        "max_records_per_device": max_records_per_device,
        "person_id": person_id,
        "name": name,
        "department": department,
        "checkpoint": checkpoint,
        "attendance_status": attendance_status,
    }
    if allowed:
        params["camera_ip"] = ",".join(allowed)
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.get(f"{base}/hikvision/attendance", params=params)
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Attendance service unavailable")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Attendance service error")
    return r.json()


@router.get("/hikvision/users")
async def get_hikvision_users(
    max_users_per_device: int = Query(2000, ge=1, le=20000),
    name: Optional[str] = Query(None),
    employee_no: Optional[str] = Query(None),
    _: dict = Depends(get_current_user),
):

    settings = get_settings()
    base = (settings.attendance_service_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="Attendance service not configured")
    allowed = _allowed_camera_ips()
    params = {
        "max_users_per_device": max_users_per_device,
        "name": name,
        "employee_no": employee_no,
    }
    if allowed:
        params["camera_ip"] = ",".join(allowed)
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.get(f"{base}/hikvision/users", params=params)
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Attendance service unavailable")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Attendance service error")
    return r.json()


@router.get("/hikvision/mappings")
async def list_hikvision_mappings(_: dict = Depends(get_current_user)):
    settings = get_settings()
    base = (settings.attendance_service_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="Attendance service not configured")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(f"{base}/hikvision/mappings")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Attendance service unavailable")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Attendance service error")
    return r.json()


@router.put("/hikvision/mappings")
async def upsert_hikvision_mapping(
    body: HikvisionMappingUpsertBody,
    _: dict = Depends(get_current_user),
):
    user = _
    role = (user.get("role") or "").strip()
    if role not in ROLES_CAN_MANAGE_HIKVISION_MAPPINGS:
        raise HTTPException(
            status_code=403,
            detail="Only administrators, partner or office manager can manage Hikvision mappings",
        )

    settings = get_settings()
    base = (settings.attendance_service_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="Attendance service not configured")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.put(
                f"{base}/hikvision/mappings",
                json=body.model_dump(exclude_none=True),
            )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Attendance service unavailable")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Attendance service error")
    return r.json()


@router.delete("/hikvision/mappings/{camera_employee_no}")
async def delete_hikvision_mapping(
    camera_employee_no: str,
    _: dict = Depends(get_current_user),
):
    user = _
    role = (user.get("role") or "").strip()
    if role not in ROLES_CAN_MANAGE_HIKVISION_MAPPINGS:
        raise HTTPException(
            status_code=403,
            detail="Only administrators, partner or office manager can manage Hikvision mappings",
        )

    settings = get_settings()
    base = (settings.attendance_service_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="Attendance service not configured")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.delete(f"{base}/hikvision/mappings/{camera_employee_no}")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Attendance service unavailable")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Attendance service error")
    return r.json()


@router.get("/explanations")
async def list_attendance_explanations(
    day: Optional[str] = Query(None),
    app_user_id: Optional[int] = Query(None),
    camera_employee_no: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    _: dict = Depends(get_current_user),
):
    settings = get_settings()
    base = (settings.attendance_service_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="Attendance service not configured")
    params = {
        "day": day,
        "app_user_id": app_user_id,
        "camera_employee_no": camera_employee_no,
        "status": status,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(f"{base}/hikvision/explanations", params=params)
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Attendance service unavailable")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Attendance service error")
    return r.json()


@router.put("/explanations")
async def upsert_attendance_explanation(
    body: AttendanceExplanationUpsertBody,
    _: dict = Depends(get_current_user),
):
    user = _
    role = (user.get("role") or "").strip()
    if role not in (
        "Главный администратор",
        "Администратор",
        "Партнер",
        "Офис менеджер",
        "Офис-менеджер",
        "Сотрудник",
    ):
        raise HTTPException(status_code=403, detail="Role is not allowed to submit explanations")

    settings = get_settings()
    base = (settings.attendance_service_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="Attendance service not configured")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.put(
                f"{base}/hikvision/explanations",
                json=body.model_dump(exclude_none=True),
            )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Attendance service unavailable")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Attendance service error")
    return r.json()


@router.post("/explanations/upload")
async def upload_attendance_explanation_photo(
    day: str = Form(...),
    camera_employee_no: str = Form(...),
    status: str = Form(...),
    app_user_id: Optional[int] = Form(None),
    file: UploadFile = File(...),
    _: dict = Depends(get_current_user),
):
    user = _
    role = (user.get("role") or "").strip()
    if role not in (
        "Главный администратор",
        "Администратор",
        "Партнер",
        "Офис менеджер",
        "Офис-менеджер",
        "Сотрудник",
    ):
        raise HTTPException(status_code=403, detail="Role is not allowed to submit explanations")

    settings = get_settings()
    base = (settings.attendance_service_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="Attendance service not configured")

    data = {
        "day": day,
        "camera_employee_no": camera_employee_no,
        "status": status,
    }
    if app_user_id is not None:
        data["app_user_id"] = str(app_user_id)
    files = {"file": (file.filename or "explanation.jpg", await file.read(), file.content_type or "application/octet-stream")}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{base}/hikvision/explanations/upload",
                data=data,
                files=files,
            )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Attendance service unavailable")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Attendance service error")
    return r.json()


@router_compat.get("/hikvision/attendance")
async def get_hikvision_attendance_compat(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    max_records_per_device: int = Query(500, ge=1, le=5000),
    person_id: Optional[str] = Query(None),
    name: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    checkpoint: Optional[str] = Query(None),
    attendance_status: Optional[str] = Query(None),
    _: dict = Depends(get_current_user),
):

    return await get_hikvision_attendance(
        date_from=date_from,
        date_to=date_to,
        max_records_per_device=max_records_per_device,
        person_id=person_id,
        name=name,
        department=department,
        checkpoint=checkpoint,
        attendance_status=attendance_status,
        _=_,
    )


@router.get("/settings/workday")
async def get_workday_settings(_: dict = Depends(get_current_user)):

    settings = get_settings()
    base = (settings.attendance_service_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="Attendance service not configured")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{base}/settings/workday")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Attendance service unavailable")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Attendance service error")
    return r.json()


@router.get("/report/daily")
async def get_daily_attendance_report(
    day: Optional[str] = Query(None, description="Дата отчёта в формате YYYY-MM-DD. По умолчанию сегодня."),
    _: dict = Depends(get_current_user),
):

    report_day = _parse_iso_date(day)
    settings = get_settings()
    base = (settings.attendance_service_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="Attendance service not configured")

    allowed = _allowed_camera_ips()
    attendance_params = {
        "date_from": report_day.isoformat(),
        "date_to": report_day.isoformat(),
        "max_records_per_device": 5000,
    }
    hikvision_users_params = {
        "max_users_per_device": 20000,
    }
    if allowed:
        attendance_params["camera_ip"] = ",".join(allowed)
        hikvision_users_params["camera_ip"] = ",".join(allowed)

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            workday_r, events_r, hikvision_users_r, mappings_r, explanations_r = await asyncio.gather(
                client.get(f"{base}/settings/workday"),
                client.get(f"{base}/hikvision/attendance", params=attendance_params),
                client.get(f"{base}/hikvision/users", params=hikvision_users_params),
                client.get(f"{base}/hikvision/mappings"),
                client.get(
                    f"{base}/hikvision/explanations",
                    params={"day": report_day.isoformat()},
                ),
            )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Attendance service unavailable")


    auth_headers = merge_upstream_headers({}) or {}
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            users_r = await client.get(
                f"{settings.auth_service_url}/users",
                params={"include_archived": False},
                headers=auth_headers,
            )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Auth service unavailable")

    if workday_r.status_code >= 400:
        raise HTTPException(status_code=workday_r.status_code, detail=workday_r.text or "Attendance service error")
    if events_r.status_code >= 400:
        raise HTTPException(status_code=events_r.status_code, detail=events_r.text or "Attendance service error")
    if hikvision_users_r.status_code >= 400:
        raise HTTPException(status_code=hikvision_users_r.status_code, detail=hikvision_users_r.text or "Attendance service error")
    if mappings_r.status_code >= 400:
        raise HTTPException(status_code=mappings_r.status_code, detail=mappings_r.text or "Attendance service error")
    if explanations_r.status_code >= 400:
        raise HTTPException(status_code=explanations_r.status_code, detail=explanations_r.text or "Attendance service error")
    if users_r.status_code >= 400:
        raise HTTPException(status_code=users_r.status_code, detail=users_r.text or "Auth service error")

    app_users = users_r.json() or []
    app_users_by_id = {u.get("id"): u for u in app_users if u.get("id") is not None}
    mappings = mappings_r.json() or []
    events_devices = events_r.json() or []
    hikvision_users_devices = hikvision_users_r.json() or []
    workday = workday_r.json() or {}
    mapping_by_employee_no = {
        (m.get("camera_employee_no") or "").strip(): m
        for m in mappings
        if (m.get("camera_employee_no") or "").strip()
    }
    explanations = explanations_r.json() or []
    explanation_by_key = {
        f"{(x.get('camera_employee_no') or '').strip()}|{(x.get('status') or '').strip().lower()}": x
        for x in explanations
        if (x.get("camera_employee_no") or "").strip()
    }

    start_val = time.fromisoformat(workday.get("workday_start", "09:00:00"))
    late_threshold = int(workday.get("late_threshold_minutes", 0) or 0)
    late_border_dt = datetime.combine(report_day, start_val) + timedelta(minutes=late_threshold)


    flat_events: list[dict] = []
    for dev in events_devices:
        for rec in (dev.get("records") or []):
            item = dict(rec)
            item["camera_ip"] = dev.get("camera_ip")
            flat_events.append(item)


    first_event_by_employee_no: dict[str, dict] = {}
    for rec in flat_events:
        employee_no = (rec.get("person_id") or "").strip()
        if not employee_no:
            continue
        dt = _parse_event_dt(rec.get("time"))
        if not dt:
            continue
        prev = first_event_by_employee_no.get(employee_no)
        if not prev or dt < prev["dt"]:
            first_event_by_employee_no[employee_no] = {"dt": dt, "record": rec}


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

        first = first_event_by_employee_no.get(employee_no)
        if not first:
            status = "absent"
            first_time = None
        else:
            first_dt = first["dt"]
            first_time = first_dt.isoformat()
            status = "late" if first_dt.replace(tzinfo=None) > late_border_dt else "present_on_time"
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


    unmapped_events = [e for e in flat_events if not e.get("mapped_app_user_id")]

    return {
        "date": report_day.isoformat(),
        "workday": {
            "workday_start": workday.get("workday_start"),
            "workday_end": workday.get("workday_end"),
            "late_threshold_minutes": workday.get("late_threshold_minutes"),
            "daily_hours_norm": workday.get("daily_hours_norm"),
            "late_border_time": late_border_dt.time().isoformat(),
        },
        "summary": {
            "total_tracked_users": len(items),
            "present_on_time": counts["present_on_time"],
            "late": counts["late"],
            "absent": counts["absent"],
            "unmapped_events": len(unmapped_events),
        },
        "items": items,
        "unmapped_events": unmapped_events,
    }


@router.get("/report/range")
async def get_attendance_range_report(
    date_from: str = Query(..., description="Дата начала в формате YYYY-MM-DD"),
    date_to: str = Query(..., description="Дата конца в формате YYYY-MM-DD"),
    _: dict = Depends(get_current_user),
):
    user = _
    _require_attendance_range_access(user)
    start = _parse_required_iso_date(date_from, field_name="date_from")
    end = _parse_required_iso_date(date_to, field_name="date_to")
    _iter_dates(start, end)

    settings = get_settings()
    base = (settings.attendance_service_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="Attendance service not configured")

    allowed = _allowed_camera_ips()
    hikvision_users_params = {"max_users_per_device": 20000}
    if allowed:
        hikvision_users_params["camera_ip"] = ",".join(allowed)

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            workday_r, hikvision_users_r, mappings_r, explanations_r = await asyncio.gather(
                client.get(f"{base}/settings/workday"),
                client.get(f"{base}/hikvision/users", params=hikvision_users_params),
                client.get(f"{base}/hikvision/mappings"),
                client.get(f"{base}/hikvision/explanations"),
            )
            events_devices = await _fetch_attendance_events_for_range(
                client,
                base,
                allowed,
                start,
                end,
            )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Attendance service unavailable")

    auth_headers = merge_upstream_headers({}) or {}
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            users_r = await client.get(
                f"{settings.auth_service_url}/users",
                params={"include_archived": False},
                headers=auth_headers,
            )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Auth service unavailable")

    if workday_r.status_code >= 400:
        raise HTTPException(status_code=workday_r.status_code, detail=workday_r.text or "Attendance service error")
    if hikvision_users_r.status_code >= 400:
        raise HTTPException(status_code=hikvision_users_r.status_code, detail=hikvision_users_r.text or "Attendance service error")
    if mappings_r.status_code >= 400:
        raise HTTPException(status_code=mappings_r.status_code, detail=mappings_r.text or "Attendance service error")
    if explanations_r.status_code >= 400:
        raise HTTPException(status_code=explanations_r.status_code, detail=explanations_r.text or "Attendance service error")
    if users_r.status_code >= 400:
        raise HTTPException(status_code=users_r.status_code, detail=users_r.text or "Auth service error")

    workday = workday_r.json() or {}
    mappings = mappings_r.json() or []
    hikvision_users_devices = hikvision_users_r.json() or []
    explanations = explanations_r.json() or []
    app_users = users_r.json() or []
    app_users_by_id = {u.get("id"): u for u in app_users if u.get("id") is not None}
    mapping_by_employee_no = {
        (m.get("camera_employee_no") or "").strip(): m
        for m in mappings
        if (m.get("camera_employee_no") or "").strip()
    }
    roster_by_employee_no = _build_roster_by_employee_no(hikvision_users_devices)
    first_events_by_day = _index_first_events_by_day(events_devices, start=start, end=end)
    items = _build_range_report_items(
        start=start,
        end=end,
        workday=workday,
        roster_by_employee_no=roster_by_employee_no,
        mapping_by_employee_no=mapping_by_employee_no,
        app_users_by_id=app_users_by_id,
        first_events_by_day=first_events_by_day,
        explanations=explanations,
    )

    return {
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "items": items,
    }


@router.patch("/settings/workday")
async def update_workday_settings(
    body: WorkdaySettingsUpdateBody,
    _: dict = Depends(get_current_user),
):

    user = _
    role = (user.get("role") or "").strip()
    if role not in ROLES_CAN_UPDATE_WORKDAY_SETTINGS:
        raise HTTPException(
            status_code=403,
            detail="Нет прав на изменение настроек рабочего дня (нужна роль администратора, партнёра, IT отдела или офис-менеджера).",
        )
    settings = get_settings()
    base = (settings.attendance_service_url or "").rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="Attendance service not configured")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.patch(
                f"{base}/settings/workday",
                json=body.model_dump(mode="json", exclude_none=True),
            )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Attendance service unavailable")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Attendance service error")
    return r.json()
