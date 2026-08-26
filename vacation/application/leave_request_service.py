from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.kind_legend import KIND_BY_KEY, REQUESTABLE_KIND_CODES
from application.vacation_balance import (
    count_calendar_days_inclusive,
    get_vacation_balance,
    validate_annual_vacation_request,
)
from backend_common.media_path import safe_media_path
from infrastructure.auth_lookup import AuthUser
from infrastructure.config import get_settings, is_managing_partner_email
from infrastructure.models import (
    LEAVE_STATUS_APPROVED,
    LEAVE_STATUS_CANCELLED,
    LEAVE_STATUS_DECLINED,
    LEAVE_STATUS_PENDING,
    LEAVE_STATUS_PENDING_FINAL,
    AbsenceDay,
    LeaveRequest,
    ScheduleEmployee,
)
from infrastructure.pdf_generation import render_leave_request_pdf

_log = logging.getLogger("vacation.leave_request")
_ANNUAL_KIND = KIND_BY_KEY["annual_vacation"]
# Версия шаблона заявления. 1 — в шапке всегда управляющий партнёр фирмы.
LEAVE_PDF_DOC_VERSION = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _count_days_inclusive(d_from: date, d_to: date) -> int:
    return count_calendar_days_inclusive(d_from, d_to)


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
    if days < 1:
        raise ValueError("Укажите корректный период (не меньше 1 календарного дня).")

    if int(kind_code) == _ANNUAL_KIND:
        years = range(date_from.year, date_to.year + 1)
        balances = {
            y: await get_vacation_balance(
                session,
                employee_user_id=employee.id,
                year=y,
            )
            for y in years
        }
        validate_annual_vacation_request(
            date_from=date_from,
            date_to=date_to,
            days_count=days,
            balances_by_year=balances,
        )

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
    try:
        subdir.mkdir(parents=True, exist_ok=True)
        name = f"leave_request_{req.id}_{uuid4().hex}.pdf"
        target = subdir / name
        target.write_bytes(pdf_bytes)
    except OSError as e:
        raise OSError(
            f"Не удалось записать PDF в MEDIA_PATH={base}: {e}. "
            "Контейнер vacation должен иметь права записи на том media (uid 10001)."
        ) from e
    return f"vacation_leave_requests/{req.created_at.year}/{req.id}/{name}"


async def render_and_attach_pdf(
    session: AsyncSession,
    req: LeaveRequest,
) -> bytes:
    pdf_bytes = render_leave_request_pdf(req)
    try:
        key = save_pdf_to_media(req, pdf_bytes)
        req.pdf_storage_key = key
        req.pdf_doc_version = LEAVE_PDF_DOC_VERSION
        req.updated_at = _utc_now()
        session.add(req)
        await session.flush()
    except OSError as e:
        # Keep the leave request; email can still attach in-memory PDF.
        _log.exception("PDF leave request not stored on disk: %s", e)
    return pdf_bytes


async def ensure_current_pdf(session: AsyncSession, req: LeaveRequest) -> bool:
    """Пересобирает устаревший документ заявки; True — если PDF перегенерирован.

    Нужно для заявок, чьи PDF были сохранены до того, как в шапке заявления
    стали печатать управляющего партнёра.
    """
    if (req.pdf_doc_version or 0) >= LEAVE_PDF_DOC_VERSION:
        return False
    await render_and_attach_pdf(session, req)
    return True


async def apply_partner_decision(
    session: AsyncSession,
    req: LeaveRequest,
    *,
    decided_by_user_id: int,
    approve: bool,
    decision_reason: str | None,
) -> LeaveRequest:
    """Первая ступень: решение курирующего партнёра.

    Если выбранный партнёр — сам управляющий, промежуточной ступени нет:
    согласие сразу утверждает заявку и открывает дни в графике.
    Иначе согласие только уводит заявку на обязательное финальное
    подтверждение, без записей в графике.
    """
    if req.status != LEAVE_STATUS_PENDING:
        return req
    now = _utc_now()
    reason = (decision_reason or "").strip()[:2000] or None
    req.decision_at = now
    req.decision_reason = reason
    req.decided_by_user_id = int(decided_by_user_id)
    req.updated_at = now
    if not approve:
        req.status = LEAVE_STATUS_DECLINED
        session.add(req)
        await session.flush()
        return req
    if is_managing_partner_email(req.partner_email):
        req.status = LEAVE_STATUS_APPROVED
        req.final_decision_at = now
        req.final_decision_reason = reason
        req.final_decided_by_user_id = int(decided_by_user_id)
        session.add(req)
        await _materialize_absence_days(session, req)
        await session.flush()
        return req
    req.status = LEAVE_STATUS_PENDING_FINAL
    session.add(req)
    await session.flush()
    return req


async def apply_final_decision(
    session: AsyncSession,
    req: LeaveRequest,
    *,
    decided_by_user_id: int | None,
    approve: bool,
    decision_reason: str | None,
) -> LeaveRequest:
    """Вторая ступень: решение управляющего партнёра, после него дни идут в график."""
    if req.status != LEAVE_STATUS_PENDING_FINAL:
        return req
    now = _utc_now()
    req.status = LEAVE_STATUS_APPROVED if approve else LEAVE_STATUS_DECLINED
    req.final_decision_at = now
    req.final_decision_reason = (decision_reason or "").strip()[:2000] or None
    req.final_decided_by_user_id = int(decided_by_user_id) if decided_by_user_id is not None else None
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


WITHDRAW_REASON = "Отозвана сотрудником"
CANCEL_AFTER_APPROVE_REASON = "Отменена сотрудником после согласования"


async def clear_absence_days(session: AsyncSession, req: LeaveRequest) -> None:
    await session.execute(
        delete(AbsenceDay).where(AbsenceDay.leave_request_id == req.id)
    )
    await session.flush()


async def cancel_leave_request(
    session: AsyncSession,
    req: LeaveRequest,
    *,
    cancelled_by_user_id: int,
    reason: str | None,
) -> LeaveRequest:
    """Отзыв pending-заявки или отмена уже одобренной.

    У одобренной заявки дни уже материализованы в графике — их нужно убрать,
    иначе отсутствие останется висеть в календаре после отмены.
    """
    if req.status not in (LEAVE_STATUS_PENDING, LEAVE_STATUS_PENDING_FINAL, LEAVE_STATUS_APPROVED):
        raise ValueError("Отменить можно только заявку на согласовании или одобренную")
    was_approved = req.status == LEAVE_STATUS_APPROVED
    now = _utc_now()
    req.status = LEAVE_STATUS_CANCELLED
    req.decision_at = now
    req.decision_reason = (reason or "").strip()[:2000] or (
        CANCEL_AFTER_APPROVE_REASON if was_approved else WITHDRAW_REASON
    )
    req.decided_by_user_id = int(cancelled_by_user_id)
    req.updated_at = now
    session.add(req)
    if was_approved:
        await clear_absence_days(session, req)
    await session.flush()
    return req


async def cleanup_pdf(req: LeaveRequest) -> None:
    if not req.pdf_storage_key:
        return
    settings = get_settings()
    target = safe_media_path(settings.media_path, req.pdf_storage_key)
    if target is not None and target.is_file():
        target.unlink(missing_ok=True)


async def delete_leave_request(session: AsyncSession, req: LeaveRequest) -> None:
    await cleanup_pdf(req)
    await session.execute(
        delete(AbsenceDay).where(AbsenceDay.leave_request_id == req.id)
    )
    await session.delete(req)
