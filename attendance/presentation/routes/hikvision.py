from datetime import date
from pathlib import Path
import uuid
from typing import Optional, List

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.hikvision_hosts import resolve_hikvision_hosts
from infrastructure.hikvision_client import get_attendance_from_devices, get_users_from_devices
from infrastructure.models import AttendanceExplanationModel, HikvisionUserBindingModel

router = APIRouter(prefix="/hikvision", tags=["hikvision"])


class HikvisionBindingUpsertBody(BaseModel):
    camera_employee_no: str
    app_user_id: int
    camera_name: Optional[str] = None


class AttendanceExplanationUpsertBody(BaseModel):
    day: str
    camera_employee_no: str
    status: str
    explanation_text: str
    app_user_id: Optional[int] = None


ALLOWED_EXPLANATION_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _parse_date(param: Optional[str]) -> Optional[date]:
    if not param:
        return None
    try:
        return date.fromisoformat(param)
    except ValueError:
        return None


def _record_matches_filters(
    rec: dict,
    person_id: Optional[str],
    name: Optional[str],
    department: Optional[str],
    checkpoint: Optional[str],
    attendance_status: Optional[str],
) -> bool:
    if person_id and (rec.get("person_id") or "").strip() != person_id.strip():
        return False
    if name:
        if name.lower() not in (rec.get("name") or "").lower():
            return False
    if department:
        if department.lower() not in (rec.get("department") or "").lower():
            return False
    if checkpoint:
        if checkpoint.lower() not in (rec.get("checkpoint") or "").lower():
            return False
    if attendance_status and (rec.get("attendance_status") or "").strip() != attendance_status.strip():
        return False
    return True


@router.get("/attendance")
async def get_attendance(
    date_from: Optional[str] = Query(None, description="Дата начала в формате YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="Дата конца в формате YYYY-MM-DD"),
    max_records_per_device: int = Query(500, ge=1, le=5000),
    person_id: Optional[str] = Query(None, description="Фильтр по идентификатору сотрудника"),
    name: Optional[str] = Query(None, description="Фильтр по имени (contains, без регистра)"),
    department: Optional[str] = Query(None, description="Фильтр по подразделению (contains, без регистра)"),
    checkpoint: Optional[str] = Query(None, description="Фильтр по названию точки прохода (contains)"),
    attendance_status: Optional[str] = Query(None, description="Фильтр по статусу посещения (например, CheckIn)"),
    camera_ip: Optional[str] = Query(None, description="Ограничить список камер (один IP или несколько через запятую)"),
    session: AsyncSession = Depends(get_session),
) -> List[dict]:

    settings = get_settings()
    try:
        hosts = resolve_hikvision_hosts(settings, camera_ip)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    if not hosts:
        raise HTTPException(status_code=400, detail="Не настроены IP камер (HIKVISION_DEVICE_IP(S)).")

    df = _parse_date(date_from)
    dt = _parse_date(date_to)
    results = get_attendance_from_devices(
        hosts=hosts,
        port=settings.hikvision_device_port,
        user=settings.hikvision_device_user,
        password=settings.hikvision_device_password,
        date_from=df,
        date_to=dt,
        max_records_per_device=max_records_per_device,
        timeout=settings.hikvision_request_timeout,
    )

    rows = await session.execute(select(HikvisionUserBindingModel))
    bindings = rows.scalars().all()
    mapping = {(b.camera_employee_no or "").strip(): b for b in bindings if (b.camera_employee_no or "").strip()}

    filtered_results: list[dict] = []
    for res in results:
        records = res.get("records") or []
        filtered = []
        for r in records:
            if not _record_matches_filters(r, person_id, name, department, checkpoint, attendance_status):
                continue
            item = dict(r)
            pid = (item.get("person_id") or "").strip()
            binding = mapping.get(pid)
            item["mapped_app_user_id"] = binding.app_user_id if binding else None
            item["mapped"] = binding is not None
            filtered.append(item)
        new_entry = dict(res)
        new_entry["records"] = filtered
        filtered_results.append(new_entry)

    return filtered_results


@router.get("/users")
async def get_users(
    max_users_per_device: int = Query(2000, ge=1, le=20000),
    name: Optional[str] = Query(None, description="Фильтр по имени (contains, без регистра)"),
    employee_no: Optional[str] = Query(None, description="Фильтр по employeeNo/табельному номеру (точное совпадение)"),
    camera_ip: Optional[str] = Query(None, description="Ограничить список камер (один IP или несколько через запятую)"),
) -> List[dict]:

    settings = get_settings()
    try:
        hosts = resolve_hikvision_hosts(settings, camera_ip)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    if not hosts:
        raise HTTPException(status_code=400, detail="Не настроены IP камер (HIKVISION_DEVICE_IP(S)).")

    results = get_users_from_devices(
        hosts=hosts,
        port=settings.hikvision_device_port,
        user=settings.hikvision_device_user,
        password=settings.hikvision_device_password,
        max_users_per_device=max_users_per_device,
        timeout=settings.hikvision_request_timeout,
        name=name,
        employee_no=employee_no,
    )
    return results


@router.get("/mappings")
async def list_mappings(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = await session.execute(
        select(HikvisionUserBindingModel).order_by(HikvisionUserBindingModel.updated_at.desc())
    )
    bindings = rows.scalars().all()
    return [
        {
            "id": b.id,
            "camera_employee_no": b.camera_employee_no,
            "app_user_id": b.app_user_id,
            "camera_name": b.camera_name,
            "created_at": b.created_at,
            "updated_at": b.updated_at,
        }
        for b in bindings
    ]


@router.put("/mappings")
async def upsert_mapping(
    body: HikvisionBindingUpsertBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    employee_no = (body.camera_employee_no or "").strip()
    if not employee_no:
        raise HTTPException(status_code=400, detail="camera_employee_no is required")


    await session.execute(
        delete(HikvisionUserBindingModel).where(
            HikvisionUserBindingModel.app_user_id == body.app_user_id,
            HikvisionUserBindingModel.camera_employee_no != employee_no,
        )
    )

    row = await session.execute(
        select(HikvisionUserBindingModel).where(HikvisionUserBindingModel.camera_employee_no == employee_no)
    )
    existing = row.scalar_one_or_none()
    if existing:
        existing.app_user_id = body.app_user_id
        existing.camera_name = (body.camera_name or "").strip() or None
        model = existing
    else:
        model = HikvisionUserBindingModel(
            camera_employee_no=employee_no,
            app_user_id=body.app_user_id,
            camera_name=(body.camera_name or "").strip() or None,
        )
        session.add(model)

    await session.commit()
    await session.refresh(model)
    return {
        "id": model.id,
        "camera_employee_no": model.camera_employee_no,
        "app_user_id": model.app_user_id,
        "camera_name": model.camera_name,
        "created_at": model.created_at,
        "updated_at": model.updated_at,
    }


@router.delete("/mappings/{camera_employee_no}")
async def delete_mapping(
    camera_employee_no: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    employee_no = (camera_employee_no or "").strip()
    if not employee_no:
        raise HTTPException(status_code=400, detail="camera_employee_no is required")

    row = await session.execute(
        select(HikvisionUserBindingModel).where(HikvisionUserBindingModel.camera_employee_no == employee_no)
    )
    existing = row.scalar_one_or_none()
    if not existing:
        raise HTTPException(status_code=404, detail="Mapping not found")

    await session.delete(existing)
    await session.commit()
    return {"ok": True}


@router.get("/explanations")
async def list_explanations(
    day: Optional[str] = Query(None, description="Дата YYYY-MM-DD"),
    app_user_id: Optional[int] = Query(None),
    camera_employee_no: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="late | absent"),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    stmt = select(AttendanceExplanationModel)
    if day:
        try:
            day_val = date.fromisoformat(day)
            stmt = stmt.where(AttendanceExplanationModel.day == day_val)
        except ValueError:
            raise HTTPException(status_code=400, detail="day must be YYYY-MM-DD")
    if app_user_id is not None:
        stmt = stmt.where(AttendanceExplanationModel.app_user_id == app_user_id)
    if camera_employee_no:
        stmt = stmt.where(AttendanceExplanationModel.camera_employee_no == camera_employee_no.strip())
    if status:
        stmt = stmt.where(AttendanceExplanationModel.status == status.strip().lower())
    stmt = stmt.order_by(AttendanceExplanationModel.updated_at.desc())
    rows = await session.execute(stmt)
    data = rows.scalars().all()
    return [
        {
            "id": x.id,
            "day": x.day.isoformat(),
            "app_user_id": x.app_user_id,
            "camera_employee_no": x.camera_employee_no,
            "status": x.status,
            "explanation_text": x.explanation_text,
            "explanation_file_path": x.explanation_file_path,
            "created_at": x.created_at,
            "updated_at": x.updated_at,
        }
        for x in data
    ]


@router.put("/explanations")
async def upsert_explanation(
    body: AttendanceExplanationUpsertBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        day_val = date.fromisoformat((body.day or "").strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="day must be YYYY-MM-DD")

    camera_employee_no = (body.camera_employee_no or "").strip()
    if not camera_employee_no:
        raise HTTPException(status_code=400, detail="camera_employee_no is required")
    status = (body.status or "").strip().lower()
    if status not in {"late", "absent"}:
        raise HTTPException(status_code=400, detail="status must be late or absent")
    explanation_text = (body.explanation_text or "").strip()
    if not explanation_text:
        raise HTTPException(status_code=400, detail="explanation_text is required")

    stmt = select(AttendanceExplanationModel).where(
        AttendanceExplanationModel.day == day_val,
        AttendanceExplanationModel.camera_employee_no == camera_employee_no,
        AttendanceExplanationModel.status == status,
    )
    row = await session.execute(stmt)
    existing = row.scalar_one_or_none()
    if existing:
        existing.app_user_id = body.app_user_id
        existing.explanation_text = explanation_text
        existing.explanation_file_path = existing.explanation_file_path
        model = existing
    else:
        model = AttendanceExplanationModel(
            day=day_val,
            app_user_id=body.app_user_id,
            camera_employee_no=camera_employee_no,
            status=status,
            explanation_text=explanation_text,
            explanation_file_path=None,
        )
        session.add(model)

    await session.commit()
    await session.refresh(model)
    return {
        "id": model.id,
        "day": model.day.isoformat(),
        "app_user_id": model.app_user_id,
        "camera_employee_no": model.camera_employee_no,
        "status": model.status,
        "explanation_text": model.explanation_text,
        "explanation_file_path": model.explanation_file_path,
        "created_at": model.created_at,
        "updated_at": model.updated_at,
    }


@router.post("/explanations/upload")
async def upload_explanation_photo(
    day: str = Form(...),
    camera_employee_no: str = Form(...),
    status: str = Form(...),
    app_user_id: Optional[int] = Form(None),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        day_val = date.fromisoformat((day or "").strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="day must be YYYY-MM-DD")

    emp_no = (camera_employee_no or "").strip()
    if not emp_no:
        raise HTTPException(status_code=400, detail="camera_employee_no is required")
    status_val = (status or "").strip().lower()
    if status_val not in {"late", "absent"}:
        raise HTTPException(status_code=400, detail="status must be late or absent")

    ext = (Path(file.filename or "").suffix or "").lower()
    if ext not in ALLOWED_EXPLANATION_PHOTO_EXTS:
        raise HTTPException(status_code=400, detail="Unsupported file extension")

    content = await file.read()
    settings = get_settings()
    max_bytes = max(1, int(settings.max_explanation_photo_size_mb)) * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=400, detail=f"File too large. Max {settings.max_explanation_photo_size_mb} MB")

    media_base = Path(settings.media_path).resolve()
    rel_dir = Path("attendance") / "explanations" / day_val.isoformat() / emp_no
    abs_dir = (media_base / rel_dir).resolve()
    if not str(abs_dir).startswith(str(media_base)):
        raise HTTPException(status_code=400, detail="Invalid media path")
    abs_dir.mkdir(parents=True, exist_ok=True)

    fname = f"{uuid.uuid4().hex}{ext}"
    abs_path = (abs_dir / fname).resolve()
    abs_path.write_bytes(content)
    rel_path = str((rel_dir / fname).as_posix())

    stmt = select(AttendanceExplanationModel).where(
        AttendanceExplanationModel.day == day_val,
        AttendanceExplanationModel.camera_employee_no == emp_no,
        AttendanceExplanationModel.status == status_val,
    )
    row = await session.execute(stmt)
    existing = row.scalar_one_or_none()
    if existing:
        existing.app_user_id = app_user_id
        existing.explanation_file_path = rel_path
        if not existing.explanation_text:
            existing.explanation_text = "Фото объяснительной загружено"
        model = existing
    else:
        model = AttendanceExplanationModel(
            day=day_val,
            app_user_id=app_user_id,
            camera_employee_no=emp_no,
            status=status_val,
            explanation_text="Фото объяснительной загружено",
            explanation_file_path=rel_path,
        )
        session.add(model)

    await session.commit()
    await session.refresh(model)
    return {
        "id": model.id,
        "day": model.day.isoformat(),
        "app_user_id": model.app_user_id,
        "camera_employee_no": model.camera_employee_no,
        "status": model.status,
        "explanation_text": model.explanation_text,
        "explanation_file_path": model.explanation_file_path,
        "created_at": model.created_at,
        "updated_at": model.updated_at,
    }
