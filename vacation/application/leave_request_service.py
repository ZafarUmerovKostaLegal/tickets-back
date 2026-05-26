from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.kind_legend import REQUESTABLE_KIND_CODES
from infrastructure.auth_lookup import AuthUser
from infrastructure.config import get_settings
from infrastructure.models import (
    LEAVE_STATUS_APPROVED,
    LEAVE_STATUS_DECLINED,
    LEAVE_STATUS_PENDING,
    AbsenceDay,
    LeaveRequest,
    ScheduleEmployee,
)
from infrastructure.pdf_generation import render_leave_request_pdf


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _count_days_inclusive(d_from: date, d_to: date) -> int:
    if d_to < d_from:
        return 0
    return (d_to - d_from).days + 1


async def _ensure_schedule_employee(
    session: AsyncSession,
    employee: AuthUser,
    *,
    year: int,
) -> ScheduleEmployee:
    r = await session.execute(
        select(ScheduleEmployee).where(
            ScheduleEmployee.year == year,
            ScheduleEmployee.auth_user_id == employee.id,
        )
    )
    row = r.scalar_one_or_none()
    if row:
        if (employee.display_name or employee.email) and row.full_name != (employee.display_name or employee.email):
            row.full_name = (employee.display_name or employee.email).strip()[:500]
        if employee.email and row.email != employee.email:
            row.email = employee.email.strip()[:320]
        return row
    row = ScheduleEmployee(
        year=year,
        excel_row_no=None,
        auth_user_id=employee.id,
        full_name=(employee.display_name or employee.email or f"User #{employee.id}").strip()[:500],
        email=(employee.email or None),
        planned_period_note=None,
    )
    session.add(row)
    await session.flush()
    return row


async def create_leave_request(
    session: AsyncSession,
    *,
    employee: AuthUser,
    partner: AuthUser,
    kind_code: int,
    date_from: date,
    date_to: date,
    reason: str | None,
) -> LeaveRequest:
    if kind_code not in REQUESTABLE_KIND_CODES:
        raise ValueError("Недопустимый вид отсутствия")
    if date_to < date_from:
        raise ValueError("date_to не может быть раньше date_from")
    if (date_to - date_from).days > 366:
        raise ValueError("Слишком длинный период")
    if (partner.role or "").strip() not in ("Партнер", "Партнёр"):
        raise ValueError("Согласующий должен быть партнёром")
    days = _count_days_inclusive(date_from, date_to)
    now = _utc_now()
    req = LeaveRequest(
        employee_user_id=employee.id,
        employee_full_name=(employee.display_name or employee.email or f"User #{employee.id}").strip()[:500],
        employee_email=(employee.email or None),
        employee_position=(employee.position or None),
        partner_user_id=partner.id,
        partner_full_name=(partner.display_name or partner.email or f"User #{partner.id}").strip()[:500],
        partner_email=(partner.email or None),
        kind_code=int(kind_code),
        date_from=date_from,
        date_to=date_to,
        days_count=days,
        reason=(reason or "").strip()[:4000] or None,
        status=LEAVE_STATUS_PENDING,
        decision_at=None,
        decision_reason=None,
        decided_by_user_id=None,
        pdf_storage_key=None,
        email_sent_at=None,
        created_at=now,
        updated_at=None,
    )
    session.add(req)
    await session.flush()
    return req


def save_pdf_to_media(req: LeaveRequest, pdf_bytes: bytes) -> str:
    settings = get_settings()
    base = Path(settings.media_path).resolve()
    subdir = base / "vacation_leave_requests" / str(req.created_at.year) / str(req.id)
    subdir.mkdir(parents=True, exist_ok=True)
    name = f"leave_request_{req.id}_{uuid4().hex}.pdf"
    target = subdir / name
    target.write_bytes(pdf_bytes)
    return f"vacation_leave_requests/{req.created_at.year}/{req.id}/{name}"


async def render_and_attach_pdf(
    session: AsyncSession,
    req: LeaveRequest,
) -> bytes:
    pdf_bytes = render_leave_request_pdf(req)
    key = save_pdf_to_media(req, pdf_bytes)
    req.pdf_storage_key = key
    req.updated_at = _utc_now()
    session.add(req)
    await session.flush()
    return pdf_bytes


async def apply_decision(
    session: AsyncSession,
    req: LeaveRequest,
    *,
    decided_by_user_id: int,
    approve: bool,
    decision_reason: str | None,
) -> LeaveRequest:
    if req.status != LEAVE_STATUS_PENDING:
        return req
    now = _utc_now()
    req.status = LEAVE_STATUS_APPROVED if approve else LEAVE_STATUS_DECLINED
    req.decision_at = now
    req.decision_reason = (decision_reason or "").strip()[:2000] or None
    req.decided_by_user_id = int(decided_by_user_id)
    req.updated_at = now
    session.add(req)
    if approve:
        await _materialize_absence_days(session, req)
    await session.flush()
    return req


async def _materialize_absence_days(
    session: AsyncSession,
    req: LeaveRequest,
) -> None:
    """После approve — создать записи в absence_days за каждый день периода.

    Использует данные из самой заявки, чтобы работать в т.ч. в callback'ах из e-mail
    (где нет JWT текущего пользователя).
    """
    pseudo_user = AuthUser(
        id=req.employee_user_id,
        email=req.employee_email or "",
        display_name=req.employee_full_name,
        picture=None,
        role="Сотрудник",
        position=req.employee_position,
        is_archived=False,
    )
    cur = req.date_from
    while cur <= req.date_to:
        emp = await _ensure_schedule_employee(session, pseudo_user, year=cur.year)
        existing = await session.execute(
            select(AbsenceDay).where(
                AbsenceDay.employee_id == emp.id,
                AbsenceDay.absence_on == cur,
            )
        )
        if not existing.scalar_one_or_none():
            session.add(
                AbsenceDay(
                    employee_id=emp.id,
                    absence_on=cur,
                    kind_code=req.kind_code,
                    leave_request_id=req.id,
                )
            )
        cur = cur + timedelta(days=1)
    await session.flush()


async def cleanup_pdf(req: LeaveRequest) -> None:
    if not req.pdf_storage_key:
        return
    settings = get_settings()
    base = Path(settings.media_path).resolve()
    target = (base / req.pdf_storage_key).resolve()
    if str(target).startswith(str(base)) and target.is_file():
        target.unlink(missing_ok=True)


async def delete_leave_request(session: AsyncSession, req: LeaveRequest) -> None:
    await cleanup_pdf(req)
    await session.execute(
        delete(AbsenceDay).where(AbsenceDay.leave_request_id == req.id)
    )
    await session.delete(req)
