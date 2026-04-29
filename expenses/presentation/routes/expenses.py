

import asyncio
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import FileResponse

from application.expense_service import (
    calc_equivalent,
    is_partner_expense,
    validate_expense_subtype_rules,
    validate_submit_fields,
)
from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.auth_users import fetch_users_by_ids
from infrastructure.expense_author_decision_notify import (
    run_author_decision_notification_safe,
    run_expense_paid_notification_safe,
)
from infrastructure.expense_submit_mail import (
    AttachmentEmailItem,
    ExpenseModerationEmailContext,
    notify_expense_submitted,
    notify_partner_expense_recorded,
)
from infrastructure.file_storage import save_attachment
from infrastructure.models import ExpenseRequestModel
from infrastructure.repositories import ExpenseRepository, _MISSING, next_kl_id
from infrastructure.tt_manager_scope import fetch_managed_scope_user_ids
from presentation.deps import (
    check_moderate_role,
    check_view_role,
    created_by_filter_for_user,
    ensure_not_moderating_own_expense,
    get_current_user,
    is_admin_editor,
    is_moderator,
    is_time_tracking_manager,
)
from presentation.schemas import (
    AttachmentOut,
    AuditLogOut,
    ExpenseAuthorSnippet,
    ExpenseCreateBody,
    ExpenseListResponse,
    ExpenseRequestDetailOut,
    ExpenseRequestListItemOut,
    ExpenseUpdateBody,
    RejectBody,
    ReviseBody,
    StatusHistoryOut,
)

router = APIRouter(prefix="/expenses", tags=["expenses"])

_log = logging.getLogger(__name__)

_MODERATION_MAIL_TIMEOUT_SEC = 90.0
_PARTNER_RECORD_MAIL_TIMEOUT_SEC = 90.0

_ALLOWED_ATTACHMENT_KINDS = frozenset({"payment_document", "payment_receipt"})

_AUTHOR_PAYMENT_DOC_STATUSES = frozenset({"draft", "revision_required", "pending_approval", "approved"})

_PAYMENT_RECEIPT_STATUSES = frozenset(
    {"draft", "revision_required", "pending_approval", "approved", "paid", "not_reimbursable"}
)
_NO_NEW_ATTACHMENT_STATUSES = frozenset({"rejected", "closed", "withdrawn"})
_MODERATOR_UPLOAD_STATUSES = frozenset({"pending_approval", "approved", "paid", "not_reimbursable"})


def _moderation_email_context(row: ExpenseRequestModel, user: dict) -> ExpenseModerationEmailContext:
    attachments = [
        AttachmentEmailItem(
            id=a.id,
            file_name=a.file_name,
            storage_key=a.storage_key,
            mime_type=a.mime_type,
            size_bytes=int(a.size_bytes or 0),
            attachment_kind=a.attachment_kind,
        )
        for a in (row.attachments or [])
    ]
    return ExpenseModerationEmailContext(
        expense_id=row.id,
        description=row.description,
        expense_date=row.expense_date,
        payment_deadline=row.payment_deadline,
        amount_uzs=row.amount_uzs,
        exchange_rate=row.exchange_rate,
        equivalent_amount=row.equivalent_amount,
        expense_type=row.expense_type,
        expense_subtype=row.expense_subtype,
        is_reimbursable=row.is_reimbursable,
        payment_method=row.payment_method,
        department_id=row.department_id,
        project_id=row.project_id,
        vendor=row.vendor,
        business_purpose=row.business_purpose,
        comment=row.comment,
        author_email=user.get("email"),
        author_name=user.get("display_name"),
        attachments=attachments,
    )


async def _run_moderation_mail(ctx: ExpenseModerationEmailContext) -> None:
    _log.info("expense moderation mail: запуск expense_id=%s", ctx.expense_id)
    try:
        await asyncio.wait_for(
            notify_expense_submitted(get_settings(), ctx),
            timeout=_MODERATION_MAIL_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        _log.error(
            "expense moderation mail: timeout after %ss expense_id=%s",
            _MODERATION_MAIL_TIMEOUT_SEC,
            ctx.expense_id,
        )
    except Exception:
        _log.exception("expense moderation mail failed expense_id=%s", ctx.expense_id)


async def _run_partner_recorded_mail(ctx: ExpenseModerationEmailContext) -> None:
    _log.info("expense partner recorded mail: запуск expense_id=%s", ctx.expense_id)
    try:
        await asyncio.wait_for(
            notify_partner_expense_recorded(get_settings(), ctx),
            timeout=_PARTNER_RECORD_MAIL_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        _log.error(
            "expense partner recorded mail: timeout after %ss expense_id=%s",
            _PARTNER_RECORD_MAIL_TIMEOUT_SEC,
            ctx.expense_id,
        )
    except Exception:
        _log.exception("expense partner recorded mail failed expense_id=%s", ctx.expense_id)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _str_val(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return str(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _can_author_edit(row: ExpenseRequestModel, user_id: int) -> bool:
    return row.created_by_user_id == user_id and row.status in ("draft", "revision_required")


async def _ensure_access(row: ExpenseRequestModel, user: dict) -> None:
    uid_filter = created_by_filter_for_user(user)
    if uid_filter is None:
        return
    if row.created_by_user_id == uid_filter:
        return
    if is_time_tracking_manager(user):
        scope = await fetch_managed_scope_user_ids(int(user["id"]))
        if row.created_by_user_id in scope:
            return
    raise HTTPException(status_code=403, detail="Нет доступа к этой заявке")


def _ensure_can_edit(row: ExpenseRequestModel, user: dict) -> None:
    uid = int(user["id"])
    if is_admin_editor(user):
        return
    if not _can_author_edit(row, uid):
        raise HTTPException(
            status_code=400,
            detail="Редактирование доступно только в статусах draft и revision_required",
        )


def _author_snippet(user_id: int, profile: dict | None) -> ExpenseAuthorSnippet:
    p = profile or {}
    return ExpenseAuthorSnippet(
        id=user_id,
        display_name=p.get("display_name"),
        email=p.get("email"),
        picture=p.get("picture"),
        position=p.get("position"),
    )


def _list_item(
    row: ExpenseRequestModel,
    author: dict | None = None,
    paid_by_profile: dict | None = None,
) -> ExpenseRequestListItemOut:
    n = len(row.attachments or [])
    a = author or {}
    created_by = ExpenseAuthorSnippet(
        id=row.created_by_user_id,
        display_name=a.get("display_name"),
        email=a.get("email"),
        picture=a.get("picture"),
        position=a.get("position"),
    )
    paid_by = (
        _author_snippet(row.paid_by_user_id, paid_by_profile)
        if row.paid_by_user_id is not None
        else None
    )
    return ExpenseRequestListItemOut(
        id=row.id,
        description=row.description,
        expense_date=row.expense_date,
        payment_deadline=row.payment_deadline,
        amount_uzs=row.amount_uzs,
        exchange_rate=row.exchange_rate,
        equivalent_amount=row.equivalent_amount,
        expense_type=row.expense_type,
        expense_subtype=row.expense_subtype,
        is_reimbursable=row.is_reimbursable,
        payment_method=row.payment_method,
        department_id=row.department_id,
        project_id=row.project_id,
        vendor=row.vendor,
        business_purpose=row.business_purpose,
        comment=row.comment,
        status=row.status,
        current_approver_id=row.current_approver_id,
        created_by_user_id=row.created_by_user_id,
        created_by=created_by,
        updated_by_user_id=row.updated_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        submitted_at=row.submitted_at,
        approved_at=row.approved_at,
        rejected_at=row.rejected_at,
        paid_at=row.paid_at,
        paid_by_user_id=row.paid_by_user_id,
        paid_by=paid_by,
        closed_at=row.closed_at,
        withdrawn_at=row.withdrawn_at,
        attachments_count=n,
    )


def _detail(
    row: ExpenseRequestModel,
    author: dict | None = None,
    paid_by_profile: dict | None = None,
) -> ExpenseRequestDetailOut:
    li = _list_item(row, author, paid_by_profile)
    sh = sorted(row.status_history or [], key=lambda x: x.changed_at)
    al = sorted(row.audit_logs or [], key=lambda x: x.performed_at)
    atts = row.attachments or []
    return ExpenseRequestDetailOut(
        **li.model_dump(),
        attachments=[
            AttachmentOut(
                id=a.id,
                expense_request_id=a.expense_request_id,
                file_name=a.file_name,
                storage_key=a.storage_key,
                mime_type=a.mime_type,
                size_bytes=a.size_bytes,
                attachment_kind=a.attachment_kind,
                uploaded_by_user_id=a.uploaded_by_user_id,
                uploaded_at=a.uploaded_at,
            )
            for a in atts
        ],
        status_history=[
            StatusHistoryOut(
                id=h.id,
                expense_request_id=h.expense_request_id,
                from_status=h.from_status,
                to_status=h.to_status,
                changed_by_user_id=h.changed_by_user_id,
                comment=h.comment,
                changed_at=h.changed_at,
            )
            for h in sh
        ],
        audit_logs=[
            AuditLogOut(
                id=log.id,
                expense_request_id=log.expense_request_id,
                action=log.action,
                field_name=log.field_name,
                old_value=log.old_value,
                new_value=log.new_value,
                performed_by_user_id=log.performed_by_user_id,
                performed_at=log.performed_at,
            )
            for log in al
        ],
    )


async def _detail_response(row: ExpenseRequestModel, authorization: Optional[str]) -> ExpenseRequestDetailOut:
    settings = get_settings()
    ids = {row.created_by_user_id}
    if row.paid_by_user_id is not None:
        ids.add(row.paid_by_user_id)
    m = await fetch_users_by_ids(settings.auth_service_url, authorization, ids)
    author = m.get(row.created_by_user_id)
    paid_by_p = m.get(row.paid_by_user_id) if row.paid_by_user_id is not None else None
    return _detail(row, author, paid_by_p)


async def _list_with_authors(
    rows: list[ExpenseRequestModel],
    total: int,
    skip: int,
    limit: int,
    authorization: Optional[str],
) -> ExpenseListResponse:
    settings = get_settings()
    ids: set[int] = set()
    for r in rows:
        ids.add(r.created_by_user_id)
        if r.paid_by_user_id is not None:
            ids.add(r.paid_by_user_id)
    m = await fetch_users_by_ids(settings.auth_service_url, authorization, ids)
    return ExpenseListResponse(
        items=[
            _list_item(
                r,
                m.get(r.created_by_user_id),
                m.get(r.paid_by_user_id) if r.paid_by_user_id is not None else None,
            )
            for r in rows
        ],
        total=total,
        skip=skip,
        limit=limit,
    )


async def _audit_diff(
    repo: ExpenseRepository,
    row: ExpenseRequestModel,
    before: dict[str, Any],
    after: dict[str, Any],
    user_id: int,
) -> None:
    for key in after:
        if key not in before:
            continue
        o, n = before[key], after[key]
        if _str_val(o) != _str_val(n):
            await repo.add_audit(
                expense_request_id=row.id,
                action="field_updated",
                field_name=key,
                old_value=_str_val(o),
                new_value=_str_val(n),
                performed_by_user_id=user_id,
            )


@router.get("", response_model=ExpenseListResponse)
async def list_expenses(
    status: Optional[str] = Query(None),
    scope: Optional[Literal["registry"]] = Query(
        None,
        description="registry — только approved, paid, closed (ТЗ §10)",
    ),
    view: Optional[Literal["timeTracking"]] = Query(
        None,
        description="timeTracking — как scope=registry: без черновиков и «на проверке» (вкладка «Расходы» в Учёте времени)",
    ),
    expense_type: Optional[str] = Query(None, alias="expenseType"),
    is_reimbursable: Optional[bool] = Query(None, alias="isReimbursable"),
    date_from: Optional[date] = Query(None, alias="dateFrom"),
    date_to: Optional[date] = Query(None, alias="dateTo"),
    department_id: Optional[str] = Query(None, alias="departmentId"),
    project_id: Optional[str] = Query(None, alias="projectId"),
    employee_user_id: Optional[int] = Query(None, alias="employeeUserId"),
    q: Optional[str] = Query(None, description="Поиск по id, описанию, контрагенту"),
    sort_by: str = Query("createdAt", alias="sortBy", pattern="^(createdAt|expenseDate|updatedAt|amountUzs|status)$"),
    sort_order: str = Query("desc", alias="sortOrder", pattern="^(asc|desc)$"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    list_scope: str | None = "registry" if view == "timeTracking" else scope
    uid_filter = created_by_filter_for_user(user)
    if uid_filter is not None:
        if is_time_tracking_manager(user) and employee_user_id is not None:
            managed_ids = await fetch_managed_scope_user_ids(int(user["id"]))
            if int(employee_user_id) not in managed_ids:
                raise HTTPException(
                    status_code=403,
                    detail="Нет доступа к заявкам выбранного пользователя (вне зоны общих проектов учёта времени)",
                )
            eff_creator = int(employee_user_id)
        else:
            eff_creator = uid_filter
    else:
        eff_creator = employee_user_id
    repo = ExpenseRepository(session)
    rows, total = await repo.list_requests(
        created_by_user_id=eff_creator,
        status=status,
        scope=list_scope,
        expense_type=expense_type,
        is_reimbursable=is_reimbursable,
        date_from=date_from,
        date_to=date_to,
        department_id=department_id,
        project_id=project_id,
        search=q,
        sort_by=sort_by,
        sort_order=sort_order,
        skip=skip,
        limit=limit,
    )
    return await _list_with_authors(rows, total, skip, limit, authorization)


@router.post("", response_model=ExpenseRequestDetailOut)
async def create_expense(
    body: ExpenseCreateBody,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    settings = get_settings()
    amount_uzs = body.amount_uzs
    exchange_rate = body.exchange_rate
    eq = calc_equivalent(amount_uzs, exchange_rate)
    exp_d = body.expense_date
    rid = await next_kl_id(session)
    uid = int(user["id"])
    repo = ExpenseRepository(session)
    partner = is_partner_expense(body.expense_type)
    now = _utc_now()
    row = await repo.create(
        id_=rid,
        description=body.description or "",
        expense_date=exp_d,
        payment_deadline=body.payment_deadline,
        amount_uzs=amount_uzs,
        exchange_rate=exchange_rate,
        equivalent_amount=eq,
        expense_type=body.expense_type,
        expense_subtype=body.expense_subtype,
        is_reimbursable=body.is_reimbursable,
        payment_method=body.payment_method,
        department_id=body.department_id,
        project_id=body.project_id,
        expense_category_id=body.expense_category_id,
        vendor=body.vendor,
        business_purpose=body.business_purpose,
        comment=body.comment,
        status="approved" if partner else "draft",
        created_by_user_id=uid,
        updated_by_user_id=uid,
    )
    await repo.add_audit(
        expense_request_id=row.id,
        action="created",
        field_name=None,
        old_value=None,
        new_value=None,
        performed_by_user_id=uid,
    )
    if partner:
        row.submitted_at = now
        row.approved_at = now
        await repo.add_status_history(
            expense_request_id=row.id,
            from_status=None,
            to_status="approved",
            changed_by_user_id=uid,
            comment="Расход партнёра — без согласования",
        )
    await session.commit()
    row = await repo.get_by_id(row.id, load_children=True)
    if partner:
        await _run_partner_recorded_mail(_moderation_email_context(row, user))
    return await _detail_response(row, authorization)


@router.get("/{expense_id}", response_model=ExpenseRequestDetailOut)
async def get_expense(
    expense_id: str,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    repo = ExpenseRepository(session)
    row = await repo.get_by_id(expense_id, load_children=True)
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    await _ensure_access(row, user)
    return await _detail_response(row, authorization)


@router.put("/{expense_id}", response_model=ExpenseRequestDetailOut)
async def update_expense(
    expense_id: str,
    body: ExpenseUpdateBody,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    repo = ExpenseRepository(session)
    row = await repo.get_by_id(expense_id, load_children=True)
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    await _ensure_access(row, user)
    _ensure_can_edit(row, user)

    before = {
        "description": row.description,
        "expense_date": row.expense_date,
        "payment_deadline": row.payment_deadline,
        "amount_uzs": row.amount_uzs,
        "exchange_rate": row.exchange_rate,
        "equivalent_amount": row.equivalent_amount,
        "expense_type": row.expense_type,
        "expense_subtype": row.expense_subtype,
        "is_reimbursable": row.is_reimbursable,
        "payment_method": row.payment_method,
        "department_id": row.department_id,
        "project_id": row.project_id,
        "vendor": row.vendor,
        "business_purpose": row.business_purpose,
        "comment": row.comment,
        "current_approver_id": row.current_approver_id,
    }
    data = body.model_dump(exclude_unset=True)
    eff_type = row.expense_type
    eff_subtype = row.expense_subtype
    if "expense_type" in data:
        eff_type = data["expense_type"]
    if "expense_subtype" in data:
        eff_subtype = data["expense_subtype"]
    try:
        validate_expense_subtype_rules(eff_type, eff_subtype)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    amount_uzs = data.get("amount_uzs", row.amount_uzs)
    exchange_rate = data.get("exchange_rate", row.exchange_rate)
    if isinstance(amount_uzs, Decimal) and amount_uzs <= 0:
        raise HTTPException(status_code=400, detail="amountUzs must be greater than 0")
    if isinstance(exchange_rate, Decimal) and exchange_rate <= 0:
        raise HTTPException(status_code=400, detail="exchangeRate must be greater than 0")
    if not isinstance(amount_uzs, Decimal):
        amount_uzs = row.amount_uzs
    if not isinstance(exchange_rate, Decimal):
        exchange_rate = row.exchange_rate
    eq = calc_equivalent(amount_uzs, exchange_rate)

    exp_d = row.expense_date
    if "expense_date" in data:
        exp_d = data["expense_date"]
    pd_new = row.payment_deadline
    if "payment_deadline" in data:
        pd_new = data["payment_deadline"]
    if pd_new is not None and exp_d is not None and pd_new < exp_d:
        raise HTTPException(
            status_code=400,
            detail="Конечный срок оплаты не может быть раньше даты расхода",
        )

    payment_deadline_arg: date | object = _MISSING
    if "payment_deadline" in data:
        payment_deadline_arg = data["payment_deadline"]

    await repo.update_fields(
        row,
        description=data.get("description"),
        expense_date=data.get("expense_date"),
        payment_deadline=payment_deadline_arg,
        amount_uzs=data.get("amount_uzs"),
        exchange_rate=data.get("exchange_rate"),
        equivalent_amount=eq,
        expense_type=data.get("expense_type"),
        expense_subtype=data.get("expense_subtype"),
        is_reimbursable=data.get("is_reimbursable"),
        payment_method=data.get("payment_method"),
        department_id=data.get("department_id"),
        project_id=data.get("project_id"),
        expense_category_id=data.get("expense_category_id"),
        vendor=data.get("vendor"),
        business_purpose=data.get("business_purpose"),
        comment=data.get("comment"),
        current_approver_id=data.get("current_approver_id"),
        updated_by_user_id=int(user["id"]),
    )
    await session.flush()
    after = {
        "description": row.description,
        "expense_date": row.expense_date,
        "payment_deadline": row.payment_deadline,
        "amount_uzs": row.amount_uzs,
        "exchange_rate": row.exchange_rate,
        "equivalent_amount": row.equivalent_amount,
        "expense_type": row.expense_type,
        "expense_subtype": row.expense_subtype,
        "is_reimbursable": row.is_reimbursable,
        "payment_method": row.payment_method,
        "department_id": row.department_id,
        "project_id": row.project_id,
        "vendor": row.vendor,
        "business_purpose": row.business_purpose,
        "comment": row.comment,
        "current_approver_id": row.current_approver_id,
    }
    await _audit_diff(repo, row, before, after, int(user["id"]))
    await session.commit()
    row = await repo.get_by_id(expense_id, load_children=True)
    return await _detail_response(row, authorization)


@router.post("/{expense_id}/submit", response_model=ExpenseRequestDetailOut)
async def submit_expense(
    expense_id: str,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    settings = get_settings()
    repo = ExpenseRepository(session)
    row = await repo.get_by_id(expense_id, load_children=True)
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    if row.created_by_user_id != int(user["id"]):
        raise HTTPException(status_code=403, detail="Отправить может только автор")
    if row.status not in ("draft", "revision_required"):
        raise HTTPException(status_code=400, detail="Отправка только из draft или revision_required")
    n_att = await repo.count_attachments(row.id)
    pd_count, pr_count, _un = await repo.attachment_kind_metrics(row.id)
    limit = settings.expense_amount_limit_uzs
    try:
        validate_submit_fields(
            description=row.description,
            expense_date=row.expense_date,
            payment_deadline=row.payment_deadline,
            amount_uzs=row.amount_uzs,
            exchange_rate=row.exchange_rate,
            expense_type=row.expense_type,
            expense_subtype=row.expense_subtype,
            is_reimbursable=row.is_reimbursable,
            comment=row.comment,
            project_id=row.project_id,
            attachment_count=n_att,
            expense_amount_limit_uzs=limit,
            payment_document_count=pd_count,
            payment_receipt_count=pr_count,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    prev = row.status
    now = _utc_now()
    uid_submit = int(user["id"])
    if is_partner_expense(row.expense_type):
        row.status = "approved"
        row.submitted_at = row.submitted_at or now
        row.approved_at = row.approved_at or now
        row.updated_by_user_id = uid_submit
        row.updated_at = now
        await repo.add_status_history(
            expense_request_id=row.id,
            from_status=prev,
            to_status="approved",
            changed_by_user_id=uid_submit,
            comment="Расход партнёра — без согласования",
        )
        await repo.add_audit(
            expense_request_id=row.id,
            action="submitted",
            field_name="status",
            old_value=prev,
            new_value="approved",
            performed_by_user_id=uid_submit,
        )
        await session.commit()
        row = await repo.get_by_id(expense_id, load_children=True)
        await _run_partner_recorded_mail(_moderation_email_context(row, user))
        return await _detail_response(row, authorization)

    row.status = "pending_approval"
    row.submitted_at = row.submitted_at or now
    row.updated_by_user_id = uid_submit
    row.updated_at = now
    await repo.add_status_history(
        expense_request_id=row.id,
        from_status=prev,
        to_status="pending_approval",
        changed_by_user_id=uid_submit,
        comment=None,
    )
    await repo.add_audit(
        expense_request_id=row.id,
        action="submitted",
        field_name="status",
        old_value=prev,
        new_value="pending_approval",
        performed_by_user_id=uid_submit,
    )
    await session.commit()
    row = await repo.get_by_id(expense_id, load_children=True)
    await _run_moderation_mail(_moderation_email_context(row, user))
    return await _detail_response(row, authorization)


@router.post("/{expense_id}/approve", response_model=ExpenseRequestDetailOut)
async def approve_expense(
    expense_id: str,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_moderate_role(user)
    repo = ExpenseRepository(session)
    row = await repo.get_by_id(expense_id, load_children=True)
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    await _ensure_access(row, user)
    if row.status == "approved":
        return await _detail_response(row, authorization)
    if row.status != "pending_approval":
        raise HTTPException(
            status_code=409,
            detail=f"Одобрение недоступно для статуса «{row.status}»",
        )
    ensure_not_moderating_own_expense(user, row.created_by_user_id)
    prev = row.status
    row.status = "approved"
    row.approved_at = _utc_now()
    row.updated_by_user_id = int(user["id"])
    row.updated_at = _utc_now()
    await repo.add_status_history(
        expense_request_id=row.id,
        from_status=prev,
        to_status="approved",
        changed_by_user_id=int(user["id"]),
        comment=None,
    )
    await repo.add_audit(
        expense_request_id=row.id,
        action="approved",
        field_name="status",
        old_value=prev,
        new_value="approved",
        performed_by_user_id=int(user["id"]),
    )
    await session.commit()
    row = await repo.get_by_id(expense_id, load_children=True)
    await run_author_decision_notification_safe(
        get_settings(),
        authorization=authorization,
        author_user_id=row.created_by_user_id,
        expense_id=row.id,
        decision="approved",
        reject_reason=None,
    )
    return await _detail_response(row, authorization)


@router.post("/{expense_id}/reject", response_model=ExpenseRequestDetailOut)
async def reject_expense(
    expense_id: str,
    body: RejectBody,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_moderate_role(user)
    repo = ExpenseRepository(session)
    row = await repo.get_by_id(expense_id, load_children=True)
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    await _ensure_access(row, user)
    if row.status == "rejected":
        return await _detail_response(row, authorization)
    if row.status != "pending_approval":
        raise HTTPException(
            status_code=409,
            detail=f"Отклонение недоступно для статуса «{row.status}»",
        )
    ensure_not_moderating_own_expense(user, row.created_by_user_id)
    prev = row.status
    row.status = "rejected"
    row.rejected_at = _utc_now()
    row.updated_by_user_id = int(user["id"])
    row.updated_at = _utc_now()
    reason = body.reason.strip()
    await repo.add_status_history(
        expense_request_id=row.id,
        from_status=prev,
        to_status="rejected",
        changed_by_user_id=int(user["id"]),
        comment=reason,
    )
    await repo.add_audit(
        expense_request_id=row.id,
        action="rejected",
        field_name="status",
        old_value=prev,
        new_value=f"rejected: {reason}",
        performed_by_user_id=int(user["id"]),
    )
    await session.commit()
    row = await repo.get_by_id(expense_id, load_children=True)
    await run_author_decision_notification_safe(
        get_settings(),
        authorization=authorization,
        author_user_id=row.created_by_user_id,
        expense_id=row.id,
        decision="rejected",
        reject_reason=reason,
    )
    return await _detail_response(row, authorization)


@router.post("/{expense_id}/revise", response_model=ExpenseRequestDetailOut)
async def revise_expense(
    expense_id: str,
    body: ReviseBody,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_moderate_role(user)
    repo = ExpenseRepository(session)
    row = await repo.get_by_id(expense_id, load_children=True)
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    await _ensure_access(row, user)
    if row.status != "pending_approval":
        raise HTTPException(
            status_code=409,
            detail=f"Возврат на доработку недоступен для статуса «{row.status}»",
        )
    ensure_not_moderating_own_expense(user, row.created_by_user_id)
    prev = row.status
    row.status = "revision_required"
    row.updated_by_user_id = int(user["id"])
    row.updated_at = _utc_now()
    c = body.comment.strip()
    await repo.add_status_history(
        expense_request_id=row.id,
        from_status=prev,
        to_status="revision_required",
        changed_by_user_id=int(user["id"]),
        comment=c,
    )
    await repo.add_audit(
        expense_request_id=row.id,
        action="revision_required",
        field_name="status",
        old_value=prev,
        new_value="revision_required",
        performed_by_user_id=int(user["id"]),
    )
    await session.commit()
    row = await repo.get_by_id(expense_id, load_children=True)
    return await _detail_response(row, authorization)


@router.post("/{expense_id}/pay", response_model=ExpenseRequestDetailOut)
async def pay_expense(
    expense_id: str,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):

    check_moderate_role(user)
    repo = ExpenseRepository(session)
    row = await repo.get_by_id(expense_id, load_children=True)
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    await _ensure_access(row, user)
    if row.status != "approved":
        raise HTTPException(status_code=400, detail="Выплата только для approved")
    ensure_not_moderating_own_expense(user, row.created_by_user_id)
    prev = row.status
    row.status = "paid"
    row.paid_at = _utc_now()
    row.paid_by_user_id = int(user["id"])
    row.updated_by_user_id = int(user["id"])
    row.updated_at = _utc_now()
    await repo.add_status_history(
        expense_request_id=row.id,
        from_status=prev,
        to_status="paid",
        changed_by_user_id=int(user["id"]),
        comment=None,
    )
    await repo.add_audit(
        expense_request_id=row.id,
        action="paid",
        field_name="status",
        old_value=prev,
        new_value="paid",
        performed_by_user_id=int(user["id"]),
    )
    await session.commit()
    row = await repo.get_by_id(expense_id, load_children=True)
    await run_expense_paid_notification_safe(
        get_settings(),
        authorization=authorization,
        author_user_id=row.created_by_user_id,
        expense_id=row.id,
        paid_by_user_id=int(user["id"]),
        paid_by_display_name=str(user.get("display_name") or "").strip() or None,
        paid_by_email=str(user.get("email") or "").strip() or None,
    )
    return await _detail_response(row, authorization)


@router.post("/{expense_id}/close", response_model=ExpenseRequestDetailOut)
async def close_expense(
    expense_id: str,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_moderate_role(user)
    repo = ExpenseRepository(session)
    row = await repo.get_by_id(expense_id, load_children=True)
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    await _ensure_access(row, user)
    ensure_not_moderating_own_expense(user, row.created_by_user_id)
    prev = row.status
    if row.status == "paid":
        new_status = "closed"
    elif row.status == "not_reimbursable":
        new_status = "closed"
    elif row.status == "approved" and not row.is_reimbursable:
        new_status = "not_reimbursable"
    else:
        raise HTTPException(
            status_code=400,
            detail="Закрытие: из paid, not_reimbursable или approved (невозмещаемый) → not_reimbursable",
        )
    row.status = new_status
    if new_status == "closed":
        row.closed_at = _utc_now()
    row.updated_by_user_id = int(user["id"])
    row.updated_at = _utc_now()
    await repo.add_status_history(
        expense_request_id=row.id,
        from_status=prev,
        to_status=new_status,
        changed_by_user_id=int(user["id"]),
        comment=None,
    )
    await repo.add_audit(
        expense_request_id=row.id,
        action="close" if new_status == "closed" else "mark_not_reimbursable",
        field_name="status",
        old_value=prev,
        new_value=new_status,
        performed_by_user_id=int(user["id"]),
    )
    await session.commit()
    row = await repo.get_by_id(expense_id, load_children=True)
    return await _detail_response(row, authorization)


@router.post("/{expense_id}/withdraw", response_model=ExpenseRequestDetailOut)
async def withdraw_expense(
    expense_id: str,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    repo = ExpenseRepository(session)
    row = await repo.get_by_id(expense_id, load_children=True)
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    if row.created_by_user_id != int(user["id"]):
        raise HTTPException(status_code=403, detail="Отозвать может только автор")
    if row.status in ("paid", "closed", "rejected", "withdrawn"):
        raise HTTPException(status_code=400, detail="Заявка уже завершена или отозвана")
    prev = row.status
    row.status = "withdrawn"
    row.withdrawn_at = _utc_now()
    row.updated_by_user_id = int(user["id"])
    row.updated_at = _utc_now()
    await repo.add_status_history(
        expense_request_id=row.id,
        from_status=prev,
        to_status="withdrawn",
        changed_by_user_id=int(user["id"]),
        comment=None,
    )
    await repo.add_audit(
        expense_request_id=row.id,
        action="withdrawn",
        field_name="status",
        old_value=prev,
        new_value="withdrawn",
        performed_by_user_id=int(user["id"]),
    )
    await session.commit()
    row = await repo.get_by_id(expense_id, load_children=True)
    return await _detail_response(row, authorization)


@router.get("/{expense_id}/attachments", response_model=list[AttachmentOut])
async def list_attachments(
    expense_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    repo = ExpenseRepository(session)
    row = await repo.get_by_id(expense_id, load_children=True)
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    await _ensure_access(row, user)
    return [
        AttachmentOut(
            id=a.id,
            expense_request_id=a.expense_request_id,
            file_name=a.file_name,
            storage_key=a.storage_key,
            mime_type=a.mime_type,
            size_bytes=a.size_bytes,
            attachment_kind=a.attachment_kind,
            uploaded_by_user_id=a.uploaded_by_user_id,
            uploaded_at=a.uploaded_at,
        )
        for a in (row.attachments or [])
    ]


@router.get("/{expense_id}/attachments/{attachment_id}/file")
async def download_attachment_file(
    expense_id: str,
    attachment_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):

    check_view_role(user)
    settings = get_settings()
    repo = ExpenseRepository(session)
    row = await repo.get_by_id(expense_id, load_children=True)
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    await _ensure_access(row, user)
    att_row = next((a for a in (row.attachments or []) if a.id == attachment_id), None)
    if not att_row:
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    p = Path(settings.media_path) / att_row.storage_key
    if not p.is_file():
        raise HTTPException(status_code=404, detail="Файл на диске не найден")
    media = (att_row.mime_type or "").strip() or "application/octet-stream"
    return FileResponse(
        path=p,
        filename=att_row.file_name or "attachment",
        media_type=media,
        content_disposition_type="inline",
    )


@router.post("/{expense_id}/attachments", response_model=ExpenseRequestDetailOut)
async def upload_attachment(
    expense_id: str,
    file: UploadFile = File(...),
    attachment_kind: Optional[str] = Form(None, alias="attachmentKind"),
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    repo = ExpenseRepository(session)
    row = await repo.get_by_id(expense_id, load_children=True)
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    await _ensure_access(row, user)
    kind_norm: str | None = None
    if attachment_kind is not None and str(attachment_kind).strip():
        k = str(attachment_kind).strip()
        if k not in _ALLOWED_ATTACHMENT_KINDS:
            raise HTTPException(status_code=400, detail="Недопустимый тип вложения")
        kind_norm = k

    if not is_admin_editor(user):
        uid = int(user["id"])
        is_author = row.created_by_user_id == uid
        moderator_may_upload = is_moderator(user) and row.status in _MODERATOR_UPLOAD_STATUSES
        if is_author:
            pass
        elif moderator_may_upload:
            ensure_not_moderating_own_expense(user, row.created_by_user_id)
        else:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Добавлять вложения может автор; модератор — в статусах на согласовании, одобрено, "
                    "выплачено, невозмещаемый (не свою заявку)"
                ),
            )

    if not is_admin_editor(user) and row.status in _NO_NEW_ATTACHMENT_STATUSES:
        raise HTTPException(status_code=400, detail="Вложения в этом статусе недоступны")

    if row.status == "paid":
        if kind_norm != "payment_receipt":
            raise HTTPException(
                status_code=400,
                detail=(
                    "После отметки оплаты можно добавлять только квитанцию об оплате "
                    "(attachmentKind=payment_receipt) — подтверждение, что платёж прошёл."
                ),
            )
    elif kind_norm == "payment_receipt":
        if row.status not in _PAYMENT_RECEIPT_STATUSES:
            raise HTTPException(
                status_code=400,
                detail="Квитанцию об оплате нельзя загрузить в текущем статусе заявки",
            )
    elif kind_norm == "payment_document" or kind_norm is None:
        if row.status == "not_reimbursable" and not is_admin_editor(user):
            raise HTTPException(
                status_code=400,
                detail="Для невозмещаемого расхода документ для оплаты не загружается",
            )
        if not is_admin_editor(user) and row.status not in _AUTHOR_PAYMENT_DOC_STATUSES:
            raise HTTPException(
                status_code=400,
                detail="Документ для оплаты в этом статусе недоступен",
            )
    elif not is_admin_editor(user):
        raise HTTPException(status_code=400, detail="Вложения в этом статусе недоступны")

    content = await file.read()
    try:
        storage_key, safe_name = save_attachment(row.id, file.filename or "file", content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    att_id = str(uuid.uuid4())
    await repo.add_attachment(
        attachment_id=att_id,
        expense_request_id=row.id,
        file_name=file.filename or safe_name,
        storage_key=storage_key,
        mime_type=file.content_type,
        size_bytes=len(content),
        uploaded_by_user_id=int(user["id"]),
        attachment_kind=kind_norm,
    )
    await repo.add_audit(
        expense_request_id=row.id,
        action="attachment_added",
        field_name="attachment",
        old_value=None,
        new_value=att_id,
        performed_by_user_id=int(user["id"]),
    )
    await session.commit()
    row = await repo.get_by_id(expense_id, load_children=True)
    return await _detail_response(row, authorization)


@router.delete("/{expense_id}/attachments/{attachment_id}", response_model=ExpenseRequestDetailOut)
async def delete_attachment(
    expense_id: str,
    attachment_id: str,
    user: dict = Depends(get_current_user),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
):
    check_view_role(user)
    settings = get_settings()
    repo = ExpenseRepository(session)
    row = await repo.get_by_id(expense_id, load_children=True)
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    await _ensure_access(row, user)
    att_row = next((a for a in (row.attachments or []) if a.id == attachment_id), None)
    if not att_row:
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    if not is_admin_editor(user):
        is_author = row.created_by_user_id == int(user["id"])
        ak = (att_row.attachment_kind or "").strip()
        moderator_may_delete_receipt = (
            is_moderator(user)
            and row.status in _MODERATOR_UPLOAD_STATUSES
            and ak == "payment_receipt"
        )
        if is_author:
            pass
        elif moderator_may_delete_receipt:
            ensure_not_moderating_own_expense(user, row.created_by_user_id)
        else:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Удалять вложения может автор; квитанцию — также модератор "
                    "(на согласовании, одобрено, выплачено, невозмещаемый, не свою заявку)"
                ),
            )
    if not is_admin_editor(user):
        ak = (att_row.attachment_kind or "").strip()
        if ak == "payment_receipt":
            if row.status not in _PAYMENT_RECEIPT_STATUSES:
                raise HTTPException(
                    status_code=400,
                    detail="Удаление квитанции в этом статусе недоступно",
                )
        elif row.status not in _AUTHOR_PAYMENT_DOC_STATUSES:
            raise HTTPException(
                status_code=400,
                detail="Удаление документа для оплаты доступно до отметки оплаты (черновик, доработка, на согласовании, одобрено)",
            )
    storage_key = att_row.storage_key
    ok = await repo.delete_attachment(expense_id, attachment_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    p = Path(settings.media_path) / storage_key
    try:
        if p.is_file():
            p.unlink()
    except OSError:
        pass
    await repo.add_audit(
        expense_request_id=row.id,
        action="attachment_deleted",
        field_name="attachment",
        old_value=attachment_id,
        new_value=None,
        performed_by_user_id=int(user["id"]),
    )
    await session.commit()
    row = await repo.get_by_id(expense_id, load_children=True)
    return await _detail_response(row, authorization)
