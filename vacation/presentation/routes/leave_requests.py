from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.kind_legend import (
    KIND_BY_CODE,
    KIND_BY_KEY,
    KIND_LEGEND_ENTRIES,
    REQUESTABLE_KIND_CODES,
)
from backend_common.media_path import safe_media_path
from application.leave_request_service import (
    apply_final_decision,
    apply_partner_decision,
    cancel_leave_request,
    create_leave_request,
    delete_leave_request,
    ensure_current_pdf,
    render_and_attach_pdf,
)
from application.vacation_balance import get_vacation_balance
from infrastructure.auth_lookup import AuthUser, get_me, get_user_public, list_partners
from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.email_action_token import STAGE_FINAL, verify_email_action_token
from infrastructure.email_send import (
    send_cancellation_to_partner,
    send_decision_to_employee,
    send_leave_request_to_managing_partner,
    send_leave_request_to_partner,
    send_partner_approval_to_employee,
)
from infrastructure.models import (
    LEAVE_STATUS_APPROVED,
    LEAVE_STATUS_CANCELLED,
    LEAVE_STATUS_DECLINED,
    LEAVE_STATUS_PENDING,
    LEAVE_STATUS_PENDING_FINAL,
    LeaveRequest,
)

router = APIRouter(tags=["leave-requests"])


async def get_current_employee(
    request: Request,
    authorization: Annotated[Optional[str], Header(alias="Authorization")] = None,
) -> AuthUser:
    if not authorization or not authorization.strip():
        raise HTTPException(status_code=401, detail="Authorization required")
    return await get_me(authorization.strip())


def _is_partner(user: AuthUser) -> bool:
    return (user.role or "").strip() in ("Партнер", "Партнёр")


def _is_managing_partner(user: AuthUser) -> bool:
    """Управляющий партнёр — вторая, обязательная ступень согласования."""
    configured = (get_settings().managing_partner_email or "").strip().casefold()
    email = (user.email or "").strip().casefold()
    return bool(configured) and email == configured


class LeaveKindOut(BaseModel):
    kind_code: int
    kind: str
    label_ru: str
    color_hex: str
    color_text_hex: str


class PartnerOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: int = Field(..., alias="userId")
    display_name: str | None = Field(None, alias="displayName")
    email: str
    picture: str | None = None
    position: str | None = None


class LeaveRequestOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    status: str
    kind_code: int = Field(..., alias="kindCode")
    kind: str
    employee_user_id: int = Field(..., alias="employeeUserId")
    employee_full_name: str = Field(..., alias="employeeFullName")
    employee_email: str | None = Field(None, alias="employeeEmail")
    employee_position: str | None = Field(None, alias="employeePosition")
    partner_user_id: int = Field(..., alias="partnerUserId")
    partner_full_name: str | None = Field(None, alias="partnerFullName")
    partner_email: str | None = Field(None, alias="partnerEmail")
    date_from: date = Field(..., alias="dateFrom")
    date_to: date = Field(..., alias="dateTo")
    days_count: int = Field(..., alias="daysCount")
    reason: str | None = None
    decision_at: datetime | None = Field(None, alias="decisionAt")
    decision_reason: str | None = Field(None, alias="decisionReason")
    final_decision_at: datetime | None = Field(None, alias="finalDecisionAt")
    final_decision_reason: str | None = Field(None, alias="finalDecisionReason")
    managing_partner_full_name: str | None = Field(None, alias="managingPartnerFullName")
    managing_partner_email: str | None = Field(None, alias="managingPartnerEmail")
    pdf_url: str | None = Field(None, alias="pdfUrl")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime | None = Field(None, alias="updatedAt")


class LeaveRequestsListOut(BaseModel):
    items: list[LeaveRequestOut]


class LeaveRequestsPendingBadgeOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    count: int
    to_decide_count: int = Field(..., alias="toDecideCount")
    mine_pending_count: int = Field(..., alias="minePendingCount")


class VacationBalanceOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    year: int
    employee_user_id: int = Field(..., alias="employeeUserId")
    entitled_days: int = Field(..., alias="entitledDays")
    used_days: int = Field(..., alias="usedDays")
    pending_days: int = Field(..., alias="pendingDays")
    remaining_days: int = Field(..., alias="remainingDays")
    continuous_14_satisfied: bool = Field(..., alias="continuous14Satisfied")
    min_continuous_days: int = Field(..., alias="minContinuousDays")
    flexible_days_max: int = Field(7, alias="flexibleDaysMax")
    flexible_days_used: int = Field(0, alias="flexibleDaysUsed")
    flexible_days_remaining: int = Field(7, alias="flexibleDaysRemaining")


class CreateLeaveRequestBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: str = Field(..., description="annual_vacation | day_off | remote_work")
    date_from: date = Field(..., alias="dateFrom")
    date_to: date = Field(..., alias="dateTo")
    partner_user_id: int = Field(..., alias="partnerUserId")
    reason: str | None = Field(None, max_length=4000)

    @model_validator(mode="after")
    def _check(self):
        if self.kind not in KIND_BY_KEY:
            allowed = ", ".join(KIND_BY_KEY.keys())
            raise ValueError(f"kind должен быть одним из: {allowed}")
        if self.date_to < self.date_from:
            raise ValueError("dateTo не может быть раньше dateFrom")
        return self


class DecisionBody(BaseModel):
    decision_reason: str | None = Field(None, max_length=2000, alias="decisionReason")


class CancelBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reason: str | None = Field(None, max_length=2000)


def _to_out(req: LeaveRequest) -> LeaveRequestOut:
    pdf_url = (
        f"/api/v1/vacations/leave-requests/{req.id}/pdf" if req.pdf_storage_key else None
    )
    settings = get_settings()
    return LeaveRequestOut(
        id=req.id,
        status=req.status,
        kind_code=req.kind_code,
        kind=KIND_BY_CODE.get(req.kind_code, "unknown"),
        employee_user_id=req.employee_user_id,
        employee_full_name=req.employee_full_name,
        employee_email=req.employee_email,
        employee_position=req.employee_position,
        partner_user_id=req.partner_user_id,
        partner_full_name=req.partner_full_name,
        partner_email=req.partner_email,
        date_from=req.date_from,
        date_to=req.date_to,
        days_count=req.days_count,
        reason=req.reason,
        decision_at=req.decision_at,
        decision_reason=req.decision_reason,
        final_decision_at=req.final_decision_at,
        final_decision_reason=req.final_decision_reason,
        managing_partner_full_name=(settings.managing_partner_name or "").strip() or None,
        managing_partner_email=(settings.managing_partner_email or "").strip() or None,
        pdf_url=pdf_url,
        created_at=req.created_at,
        updated_at=req.updated_at,
    )


@router.get("/leave-kinds", response_model=list[LeaveKindOut])
async def get_leave_kinds():
    return [
        LeaveKindOut(**e.model_dump())
        for e in KIND_LEGEND_ENTRIES
        if e.kind_code in REQUESTABLE_KIND_CODES
    ]


@router.get("/leave-balance", response_model=VacationBalanceOut)
async def get_leave_balance(
    year: int | None = Query(None, ge=2000, le=2100, description="Год учёта; по умолчанию текущий"),
    employee: AuthUser = Depends(get_current_employee),
    session: AsyncSession = Depends(get_session),
):
    """Положенные / использованные / остаток дней ежегодного отпуска и статус обязательных 14 дней."""
    y = int(year) if year is not None else date.today().year
    bal = await get_vacation_balance(session, employee_user_id=employee.id, year=y)
    return VacationBalanceOut(
        year=bal.year,
        employee_user_id=bal.employee_user_id,
        entitled_days=bal.entitled_days,
        used_days=bal.used_days,
        pending_days=bal.pending_days,
        remaining_days=bal.remaining_days,
        continuous_14_satisfied=bal.continuous_14_satisfied,
        min_continuous_days=bal.min_continuous_days,
        flexible_days_max=bal.flexible_days_max,
        flexible_days_used=bal.flexible_days_used,
        flexible_days_remaining=bal.flexible_days_remaining,
    )


@router.get("/partners", response_model=list[PartnerOut])
async def get_partners(
    request: Request,
    authorization: Annotated[Optional[str], Header(alias="Authorization")] = None,
    _: AuthUser = Depends(get_current_employee),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")
    partners = await list_partners(authorization.strip())
    return [
        PartnerOut(
            user_id=p.id,
            display_name=p.display_name,
            email=p.email,
            picture=p.picture,
            position=p.position,
        )
        for p in partners
    ]


@router.post("/leave-requests", response_model=LeaveRequestOut, status_code=201)
async def post_leave_request(
    body: CreateLeaveRequestBody,
    request: Request,
    authorization: Annotated[Optional[str], Header(alias="Authorization")] = None,
    employee: AuthUser = Depends(get_current_employee),
    session: AsyncSession = Depends(get_session),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")
    partner = await get_user_public(body.partner_user_id, authorization.strip())
    if partner is None:
        raise HTTPException(status_code=404, detail="Партнёр не найден")
    if not _is_partner(partner):
        raise HTTPException(status_code=400, detail="Выбранный пользователь не является партнёром")
    try:
        req = await create_leave_request(
            session,
            employee=employee,
            partner=partner,
            kind_code=KIND_BY_KEY[body.kind],
            date_from=body.date_from,
            date_to=body.date_to,
            reason=body.reason,
        )
        pdf_bytes = await render_and_attach_pdf(session, req)
        await session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось создать заявку на отпуск: {exc}",
        ) from exc

    sent = await send_leave_request_to_partner(req, pdf_bytes)
    if sent:
        req.email_sent_at = datetime.now(timezone.utc)
        session.add(req)
        await session.commit()

    return _to_out(req)


@router.get("/leave-requests", response_model=LeaveRequestsListOut)
async def list_leave_requests(
    scope: Literal["mine", "to_decide", "all"] = Query("mine"),
    status: Literal["pending", "pending_final", "approved", "declined", "cancelled", "any"] = Query("any"),
    employee: AuthUser = Depends(get_current_employee),
    session: AsyncSession = Depends(get_session),
):
    q = select(LeaveRequest)
    managing = _is_managing_partner(employee)
    if scope == "mine":
        q = q.where(LeaveRequest.employee_user_id == employee.id)
    elif scope == "to_decide":
        if not _is_partner(employee) and not managing:
            raise HTTPException(status_code=403, detail="Только партнёры могут видеть заявки на согласование")
        if managing:
            # Управляющий партнёр решает вторую ступень по всем заявкам фирмы.
            q = q.where(
                or_(
                    LeaveRequest.partner_user_id == employee.id,
                    LeaveRequest.status == LEAVE_STATUS_PENDING_FINAL,
                )
            )
        else:
            q = q.where(LeaveRequest.partner_user_id == employee.id)
    else:
        if not _is_partner(employee):
            q = q.where(
                or_(
                    LeaveRequest.employee_user_id == employee.id,
                    LeaveRequest.partner_user_id == employee.id,
                )
            )
    if status != "any":
        q = q.where(LeaveRequest.status == status)
    q = q.order_by(LeaveRequest.created_at.desc())
    r = await session.execute(q)
    return LeaveRequestsListOut(items=[_to_out(x) for x in r.scalars().all()])


@router.get("/leave-requests/pending/badge", response_model=LeaveRequestsPendingBadgeOut)
async def leave_requests_pending_badge(
    employee: AuthUser = Depends(get_current_employee),
    session: AsyncSession = Depends(get_session),
):
    to_decide = 0
    if _is_partner(employee):
        r = await session.execute(
            select(func.count())
            .select_from(LeaveRequest)
            .where(
                LeaveRequest.partner_user_id == employee.id,
                LeaveRequest.status == LEAVE_STATUS_PENDING,
            )
        )
        to_decide = int(r.scalar() or 0)
    if _is_managing_partner(employee):
        r_final = await session.execute(
            select(func.count())
            .select_from(LeaveRequest)
            .where(LeaveRequest.status == LEAVE_STATUS_PENDING_FINAL)
        )
        to_decide += int(r_final.scalar() or 0)

    r_mine = await session.execute(
        select(func.count())
        .select_from(LeaveRequest)
        .where(
            LeaveRequest.employee_user_id == employee.id,
            LeaveRequest.status.in_((LEAVE_STATUS_PENDING, LEAVE_STATUS_PENDING_FINAL)),
        )
    )
    mine_pending = int(r_mine.scalar() or 0)

    return LeaveRequestsPendingBadgeOut(
        count=to_decide,
        to_decide_count=to_decide,
        mine_pending_count=mine_pending,
    )


def _can_read_request(req: LeaveRequest, user: AuthUser) -> bool:
    return (
        req.employee_user_id == user.id
        or req.partner_user_id == user.id
        or _is_partner(user)
        or _is_managing_partner(user)
    )


async def _load_request(session: AsyncSession, request_id: int) -> LeaveRequest:
    r = await session.execute(select(LeaveRequest).where(LeaveRequest.id == request_id))
    row = r.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    return row


                                                                                  
                                                                                                
@router.get("/leave-requests/email-action")
async def email_action(
    token: str = Query(...),
    confirm: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    settings = get_settings()
    try:
        payload = verify_email_action_token(settings.email_action_secret, token)
    except ValueError as exc:
        return HTMLResponse(
            content=f"<html><body><h2>Ссылка недействительна</h2><p>{exc}</p></body></html>",
            status_code=400,
        )
    rid = int(payload["rid"])
    act = payload["act"]
    stage = payload["stg"]
    req = await _load_request(session, rid)
    expected_status = LEAVE_STATUS_PENDING_FINAL if stage == STAGE_FINAL else LEAVE_STATUS_PENDING
    if req.status != expected_status:
        return HTMLResponse(
            content=(
                f"<html><body><h2>Заявка #{rid} уже обработана</h2>"
                f"<p>Текущий статус: <b>{req.status}</b>.</p></body></html>"
            ),
            status_code=200,
        )
    if settings.email_action_confirm_step and not confirm:
        title = "Утвердить заявку" if act == "approve" else "Отклонить заявку"
        return HTMLResponse(
            content=f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8"><title>{title}</title></head>
<body style="font-family:Segoe UI,Arial,sans-serif;background:#f1f5f9;padding:32px;">
<div style="max-width:520px;margin:0 auto;background:#ffffff;border-radius:12px;padding:24px;border:1px solid #e2e8f0;">
<h2 style="margin:0 0 12px 0;">{title} #{rid}</h2>
<p>Сотрудник: <b>{req.employee_full_name}</b></p>
<p>Период: {req.date_from.strftime('%d.%m.%Y')} — {req.date_to.strftime('%d.%m.%Y')}</p>
<p style="margin:18px 0 8px 0;">Подтвердите действие:</p>
<a href="?token={token}&confirm=1" style="display:inline-block;padding:10px 16px;border-radius:8px;background:{'#16a34a' if act == 'approve' else '#dc2626'};color:#fff;text-decoration:none;font-weight:600;">{title}</a>
</div></body></html>""",
            status_code=200,
        )
    approve = act == "approve"
    if stage == STAGE_FINAL:
        req = await apply_final_decision(
            session,
            req,
            # Ссылка из письма не даёт токена управляющего партнёра — автора решения не пишем.
            decided_by_user_id=None,
            approve=approve,
            decision_reason="Финальное решение через e-mail",
        )
        await session.commit()
        await send_decision_to_employee(req)
        note = "Сотруднику отправлено уведомление."
    else:
        req = await apply_partner_decision(
            session,
            req,
            decided_by_user_id=req.partner_user_id,
            approve=approve,
            decision_reason="Решение через e-mail",
        )
        pdf_bytes: bytes | None = None
        if req.status == LEAVE_STATUS_PENDING_FINAL:
            pdf_bytes = await render_and_attach_pdf(session, req)
        await session.commit()
        if req.status == LEAVE_STATUS_PENDING_FINAL:
            await send_leave_request_to_managing_partner(req, pdf_bytes)
            await send_partner_approval_to_employee(req)
            note = "Заявка отправлена на финальное подтверждение управляющему партнёру."
        else:
            await send_decision_to_employee(req)
            note = "Сотруднику отправлено уведомление."
    if req.status == LEAVE_STATUS_APPROVED:
        msg = "утверждена"
    elif req.status == LEAVE_STATUS_PENDING_FINAL:
        msg = "согласована"
    else:
        msg = "отклонена"
    return HTMLResponse(
        content=(
            f"<html><body style='font-family:Segoe UI,Arial,sans-serif;padding:24px;'>"
            f"<h2>Заявка #{rid} {msg}</h2>"
            f"<p>{note}</p>"
            f"</body></html>"
        ),
        status_code=200,
    )


@router.get("/leave-requests/{request_id}", response_model=LeaveRequestOut)
async def get_leave_request(
    request_id: int,
    employee: AuthUser = Depends(get_current_employee),
    session: AsyncSession = Depends(get_session),
):
    req = await _load_request(session, request_id)
    if not _can_read_request(req, employee):
        raise HTTPException(status_code=403, detail="Нет доступа к заявке")
    return _to_out(req)


@router.get("/leave-requests/{request_id}/pdf")
async def get_leave_request_pdf(
    request_id: int,
    employee: AuthUser = Depends(get_current_employee),
    session: AsyncSession = Depends(get_session),
):
    req = await _load_request(session, request_id)
    if not _can_read_request(req, employee):
        raise HTTPException(status_code=403, detail="Нет доступа к заявке")
    if not req.pdf_storage_key:
        raise HTTPException(status_code=404, detail="PDF не сформирован")
    # Заявления, сохранённые старым шаблоном, пересобираем — в шапке должен
    # стоять управляющий партнёр.
    if await ensure_current_pdf(session, req):
        await session.commit()
    settings = get_settings()
    target = safe_media_path(settings.media_path, req.pdf_storage_key)
    if target is None or not target.is_file():
        raise HTTPException(status_code=404, detail="PDF файл недоступен")
    return FileResponse(target, media_type="application/pdf", filename=f"leave_request_{req.id}.pdf")


async def _decide(
    session: AsyncSession,
    request_id: int,
    employee: AuthUser,
    *,
    approve: bool,
    decision_reason: str | None,
) -> LeaveRequestOut:
    """Решение по заявке: ступень определяется её текущим статусом.

    pending → решает курирующий партнёр. Если это сам управляющий партнёр,
    согласие сразу утверждает заявку; иначе заявка уходит на pending_final.
    pending_final → решает управляющий партнёр, и только его согласие
    открывает дни в графике.
    """
    req = await _load_request(session, request_id)
    if req.status == LEAVE_STATUS_PENDING:
        if req.partner_user_id != employee.id:
            raise HTTPException(status_code=403, detail="Решение может принять только курирующий партнёр")
        req = await apply_partner_decision(
            session,
            req,
            decided_by_user_id=employee.id,
            approve=approve,
            decision_reason=decision_reason,
        )
        pdf_bytes: bytes | None = None
        if req.status == LEAVE_STATUS_PENDING_FINAL:
            # Документ пересобираем, чтобы у управляющего партнёра был
            # актуальный PDF с его ФИО в шапке заявления.
            pdf_bytes = await render_and_attach_pdf(session, req)
        await session.commit()
        if req.status == LEAVE_STATUS_PENDING_FINAL:
            await send_leave_request_to_managing_partner(req, pdf_bytes)
            await send_partner_approval_to_employee(req)
        else:
            await send_decision_to_employee(req)
        return _to_out(req)

    if req.status == LEAVE_STATUS_PENDING_FINAL:
        if not _is_managing_partner(employee):
            raise HTTPException(
                status_code=403,
                detail="Финальное решение принимает управляющий партнёр",
            )
        req = await apply_final_decision(
            session,
            req,
            decided_by_user_id=employee.id,
            approve=approve,
            decision_reason=decision_reason,
        )
        await session.commit()
        await send_decision_to_employee(req)
        return _to_out(req)

    raise HTTPException(status_code=409, detail=f"Заявка уже {req.status}")


@router.post("/leave-requests/{request_id}/approve", response_model=LeaveRequestOut)
async def approve_request(
    request_id: int,
    body: DecisionBody,
    employee: AuthUser = Depends(get_current_employee),
    session: AsyncSession = Depends(get_session),
):
    return await _decide(
        session,
        request_id,
        employee,
        approve=True,
        decision_reason=body.decision_reason,
    )


@router.post("/leave-requests/{request_id}/decline", response_model=LeaveRequestOut)
async def decline_request(
    request_id: int,
    body: DecisionBody,
    employee: AuthUser = Depends(get_current_employee),
    session: AsyncSession = Depends(get_session),
):
    return await _decide(
        session,
        request_id,
        employee,
        approve=False,
        decision_reason=body.decision_reason,
    )


@router.post("/leave-requests/{request_id}/withdraw", response_model=LeaveRequestOut)
async def withdraw_request(
    request_id: int,
    body: CancelBody | None = None,
    employee: AuthUser = Depends(get_current_employee),
    session: AsyncSession = Depends(get_session),
):
    """Отзыв заявки автором, пока она не прошла обе ступени согласования."""
    req = await _load_request(session, request_id)
    if req.employee_user_id != employee.id:
        raise HTTPException(status_code=403, detail="Отозвать можно только свою заявку")
    if req.status not in (LEAVE_STATUS_PENDING, LEAVE_STATUS_PENDING_FINAL):
        raise HTTPException(
            status_code=409,
            detail="Отозвать можно только заявку на согласовании",
        )
    was_pending_final = req.status == LEAVE_STATUS_PENDING_FINAL
    req = await cancel_leave_request(
        session,
        req,
        cancelled_by_user_id=employee.id,
        reason=body.reason if body else None,
    )
    await session.commit()
    await send_cancellation_to_partner(
        req,
        before_decision=True,
        also_managing_partner=was_pending_final,
    )
    return _to_out(req)


@router.post("/leave-requests/{request_id}/cancel", response_model=LeaveRequestOut)
async def cancel_approved_request(
    request_id: int,
    body: CancelBody | None = None,
    employee: AuthUser = Depends(get_current_employee),
    session: AsyncSession = Depends(get_session),
):
    """Отмена уже согласованного отсутствия: дни убираются из графика."""
    req = await _load_request(session, request_id)
    if req.employee_user_id != employee.id:
        raise HTTPException(status_code=403, detail="Отменить можно только свою заявку")
    if req.status in (LEAVE_STATUS_PENDING, LEAVE_STATUS_PENDING_FINAL):
        raise HTTPException(
            status_code=409,
            detail="Заявка ещё на согласовании — её можно отозвать",
        )
    if req.status != LEAVE_STATUS_APPROVED:
        raise HTTPException(
            status_code=409,
            detail="Отменить можно только одобренную заявку",
        )
    req = await cancel_leave_request(
        session,
        req,
        cancelled_by_user_id=employee.id,
        reason=body.reason if body else None,
    )
    await session.commit()
    await send_cancellation_to_partner(req, before_decision=False)
    return _to_out(req)


@router.delete("/leave-requests/{request_id}", status_code=204)
async def delete_request(
    request_id: int,
    employee: AuthUser = Depends(get_current_employee),
    session: AsyncSession = Depends(get_session),
):
    """Полное удаление: запись, PDF и дни в графике.

    Автор может удалить только отозванную или отклонённую заявку.
    Партнёр (и управляющий партнёр) может удалить любую заявку.
    """
    req = await _load_request(session, request_id)
    partner = _is_partner(employee) or _is_managing_partner(employee)
    if req.employee_user_id != employee.id and not partner:
        raise HTTPException(status_code=403, detail="Удалить можно только свою заявку")
    if not partner and req.status not in (LEAVE_STATUS_CANCELLED, LEAVE_STATUS_DECLINED):
        raise HTTPException(
            status_code=409,
            detail="Сначала отзовите или отмените заявку, потом её можно удалить",
        )
    await delete_leave_request(session, req)
    await session.commit()


