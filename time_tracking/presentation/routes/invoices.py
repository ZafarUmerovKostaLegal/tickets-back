

from __future__ import annotations

import json
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from application.invoice_service import (
    cancel_invoice,
    create_invoice,
    delete_draft_invoice,
    get_invoices_aggregated_stats,
    invoice_to_dict,
    invoice_to_dict_async,
    list_unbilled_expenses,
    list_unbilled_time_entries,
    mark_viewed,
    patch_invoice_draft,
    register_payment,
    record_payment_confirmation_document,
    send_invoice,
)
from application.partner_snapshot_invoice_preview import (
    resolve_partner_invoice_preview,
)
from infrastructure.database import get_session
from infrastructure.models import TimeManagerClientModel, TimeManagerClientProjectModel
from infrastructure.repository_invoices import InvoiceRepository
from infrastructure.repository_partner_report_confirmations import (
    PartnerReportConfirmationRepository,
)
from presentation.deps import invoice_actor_auth_user_id
from presentation.schemas_invoices import (
    InvoiceCreateBody,
    InvoicePatchBody,
    InvoicePaymentBody,
    InvoicePaymentConfirmationBody,
)

router = APIRouter(prefix="/invoices", tags=["invoices"])

_INVOICE_NO_STORE_HEADERS = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}


def _invoice_json_response(payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        content=jsonable_encoder(payload),
        headers=_INVOICE_NO_STORE_HEADERS,
    )


@router.get("/unbilled-time")
async def unbilled_time(
    project_id: str = Query(..., alias="projectId"),
    date_from: date = Query(..., alias="dateFrom"),
    date_to: date = Query(..., alias="dateTo"),
    session: AsyncSession = Depends(get_session),
):
    if date_to < date_from:
        raise HTTPException(status_code=400, detail="dateTo < dateFrom")
    return await list_unbilled_time_entries(
        session, project_id=project_id, date_from=date_from, date_to=date_to,
    )


@router.get("/unbilled-expenses")
async def unbilled_expenses(
    project_id: str = Query(..., alias="projectId"),
    date_from: date = Query(..., alias="dateFrom"),
    date_to: date = Query(..., alias="dateTo"),
    session: AsyncSession = Depends(get_session),
):
    if date_to < date_from:
        raise HTTPException(status_code=400, detail="dateTo < dateFrom")
    return await list_unbilled_expenses(
        session, project_id=project_id, date_from=date_from, date_to=date_to,
    )


@router.post("/fx-rates/ensure")
async def ensure_invoice_fx_rates(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
):
    """Seed time_tracking_fx_rates from client CBU payload and/or live CBU fetch."""
    from application.invoice_fx import ensure_fx_book_for_dates, upsert_fx_rates_payload

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Ожидается JSON-объект")

    raw_rates = body.get("rates")
    written = 0
    if isinstance(raw_rates, list) and raw_rates:
        written = await upsert_fx_rates_payload(session, raw_rates)

    raw_dates = body.get("dates")
    parsed: list[date] = []
    if isinstance(raw_dates, list):
        for item in raw_dates:
            s = str(item or "").strip()[:10]
            if len(s) != 10:
                continue
            try:
                parsed.append(date.fromisoformat(s))
            except ValueError:
                continue
    if isinstance(raw_rates, list):
        for item in raw_rates:
            if not isinstance(item, dict):
                continue
            s = str(item.get("rateDate") or item.get("rate_date") or "").strip()[:10]
            if len(s) != 10:
                continue
            try:
                parsed.append(date.fromisoformat(s))
            except ValueError:
                continue

    target = str(body.get("currency") or body.get("targetCurrency") or "UZS").strip().upper()[:10] or "UZS"
    if parsed:
        await ensure_fx_book_for_dates(
            session,
            parsed,
            required_pairs=[("USD", "UZS"), ("UZS", "USD"), ("USD", target), ("UZS", target)],
        )
    elif written == 0:
        raise HTTPException(status_code=400, detail="Передайте dates: string[] и/или rates: [...]")

    await session.commit()
    return {
        "ok": True,
        "dates": [d.isoformat() for d in sorted(set(parsed))],
        "currency": target,
        "upserted": written,
    }


@router.get("/from-partner-period/preview")
async def partner_period_invoice_preview(
    project_id: str = Query(..., alias="projectId"),
    date_from: date = Query(..., alias="dateFrom"),
    date_to: date = Query(..., alias="dateTo"),
    currency: Optional[str] = Query(None),
    issue_date: Optional[date] = Query(None, alias="issueDate"),
    client_id: Optional[str] = Query(None, alias="clientId"),
    session: AsyncSession = Depends(get_session),
):
    """Invoice-ready subtotal for a partner-confirmed period (time + package + expenses, FX)."""
    if date_to < date_from:
        raise HTTPException(status_code=400, detail="dateTo < dateFrom")
    pid = project_id.strip()
    inv_ccy = (currency or "").strip().upper()[:10] or None
    if not inv_ccy and client_id:
        client = await session.get(TimeManagerClientModel, client_id.strip())
        if client:
            inv_ccy = (client.currency or "USD").strip().upper()[:10] or "USD"
    if not inv_ccy:
        proj = await session.get(TimeManagerClientProjectModel, pid)
        if proj:
            client = await session.get(TimeManagerClientModel, proj.client_id)
            if client:
                inv_ccy = (client.currency or "USD").strip().upper()[:10] or "USD"
            else:
                inv_ccy = (getattr(proj, "currency", None) or "USD").strip().upper()[:10] or "USD"
        else:
            inv_ccy = "USD"
    preview = await resolve_partner_invoice_preview(
        session,
        project_id=pid,
        date_from=date_from,
        date_to=date_to,
        invoice_currency=inv_ccy,
        issue_date=issue_date or date_to,
        exclude_invoiced=True,
    )
    return preview.as_dict()


@router.get("/stats")
async def invoices_stats(
    session: AsyncSession = Depends(get_session),
    client_id: Optional[str] = Query(None, alias="clientId"),
    project_id: Optional[str] = Query(None, alias="projectId"),
    status: Optional[str] = Query(
        None,
        description="draft|sent|viewed|partial_paid|paid|canceled|overdue|… — тот же фильтр, что и у списка",
    ),
    date_from: Optional[date] = Query(None, alias="dateFrom"),
    date_to: Optional[date] = Query(None, alias="dateTo"),
):

    stats = await get_invoices_aggregated_stats(
        session,
        client_id=client_id,
        project_id=project_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    return _invoice_json_response(stats)


@router.get("")
async def list_invoices(
    session: AsyncSession = Depends(get_session),
    client_id: Optional[str] = Query(None, alias="clientId"),
    project_id: Optional[str] = Query(None, alias="projectId"),
    status: Optional[str] = Query(None, description="draft|sent|viewed|partial_paid|paid|canceled|overdue"),
    date_from: Optional[date] = Query(None, alias="dateFrom"),
    date_to: Optional[date] = Query(None, alias="dateTo"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    include_total: bool = Query(False, alias="includeTotalCount"),
    partner_billing_project_id: Optional[str] = Query(None, alias="partnerBillingProjectId"),
    partner_billing_period_from: Optional[date] = Query(None, alias="partnerBillingPeriodFrom"),
    partner_billing_period_to: Optional[date] = Query(None, alias="partnerBillingPeriodTo"),
):
    trio_set = (
        partner_billing_project_id
        and partner_billing_period_from is not None
        and partner_billing_period_to is not None
    )
    if trio_set:
        if partner_billing_period_to < partner_billing_period_from:
            raise HTTPException(status_code=400, detail="partnerBillingPeriodTo < partnerBillingPeriodFrom")
        conf_repo = PartnerReportConfirmationRepository(session)
        if not await conf_repo.has_fully_confirmed_for_project_period(
            partner_billing_project_id.strip(),
            partner_billing_period_from,
            partner_billing_period_to,
        ):
            payload: dict = {
                "items": [],
                "limit": limit,
                "offset": offset,
                "partnerConfirmationBlocked": True,
            }
            if include_total:
                payload["totalCount"] = 0
            return _invoice_json_response(payload)

    repo = InvoiceRepository(session)
    rows = await repo.list_invoices(
        client_id=client_id,
        project_id=project_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    out = []
    for inv in rows:
        out.append(invoice_to_dict(inv, include_lines=False, include_payments=False))
    payload: dict = {"items": out, "limit": limit, "offset": offset}
    if include_total:
        payload["totalCount"] = await repo.count_invoices(
            client_id=client_id,
            project_id=project_id,
            status=status,
            date_from=date_from,
            date_to=date_to,
        )
    return _invoice_json_response(payload)


@router.post("", status_code=201)
async def create_invoice_route(
    body: InvoiceCreateBody,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(invoice_actor_auth_user_id),
):
    lines_payload: list[dict[str, Any]] | None = None
    if body.lines:
        lines_payload = [ln.model_dump(mode="json", by_alias=False, exclude_none=True) for ln in body.lines]
    inv = await create_invoice(
        session,
        actor_auth_user_id=actor,
        client_id=body.client_id,
        project_id=body.project_id,
        issue_date=body.issue_date,
        due_date=body.due_date,
        currency=body.currency,
        tax_percent=body.tax_percent,
        tax2_percent=body.tax2_percent,
        discount_percent=body.discount_percent,
        client_note=body.client_note,
        internal_note=body.internal_note,
        lines=lines_payload,
        time_entry_ids=body.time_entry_ids,
        expense_ids=body.expense_ids,
        partner_billing_period_from=body.partner_billing_period_from,
        partner_billing_period_to=body.partner_billing_period_to,
        invoice_number=body.invoice_number,
        partner_confirmation_request_id=body.partner_confirmation_request_id,
    )
    await session.commit()
    inv2 = await InvoiceRepository(session).get_with_children(inv.id)
    assert inv2
    return await invoice_to_dict_async(session, inv2, include_lines=True, include_payments=True)


@router.get("/{invoice_id}/audit")
async def list_audit(
    invoice_id: str,
    session: AsyncSession = Depends(get_session),
):
    inv = await InvoiceRepository(session).get_with_children(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    logs = sorted(inv.audit_logs or [], key=lambda x: x.created_at)
    return [
        {
            "id": log.id,
            "action": log.action,
            "detail": log.detail,
            "actorAuthUserId": log.actor_auth_user_id,
            "createdAt": log.created_at.isoformat(),
        }
        for log in logs
    ]


@router.get("/{invoice_id}")
async def get_invoice(
    invoice_id: str,
    session: AsyncSession = Depends(get_session),
    include_payments: bool = Query(True, alias="includePayments"),
):
    repo = InvoiceRepository(session)
    inv = await repo.get_with_children(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    await repo.reconcile_paid_fields(inv)
    await session.commit()
    session.expire_all()
    inv = await repo.get_with_children(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    payload = await invoice_to_dict_async(session, inv, include_lines=True, include_payments=include_payments)
    return _invoice_json_response(payload)


@router.patch("/{invoice_id}")
async def patch_invoice_route(
    invoice_id: str,
    body: InvoicePatchBody,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(invoice_actor_auth_user_id),
):
    inv = await InvoiceRepository(session).get_with_children(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    inv = await patch_invoice_draft(
        session,
        inv,
        actor_auth_user_id=actor,
        issue_date=body.issue_date,
        due_date=body.due_date,
        client_note=body.client_note,
        internal_note=body.internal_note,
        tax_percent=body.tax_percent,
        tax2_percent=body.tax2_percent,
        discount_percent=body.discount_percent,
        project_id=body.project_id,
        replace_lines=body.lines,
    )
    await session.commit()
    inv2 = await InvoiceRepository(session).get_with_children(invoice_id)
    assert inv2
    return await invoice_to_dict_async(session, inv2, include_lines=True, include_payments=True)


@router.post("/{invoice_id}/send")
async def send_invoice_route(
    invoice_id: str,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(invoice_actor_auth_user_id),
):
    inv = await InvoiceRepository(session).get_with_children(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    inv = await send_invoice(session, inv, actor_auth_user_id=actor)
    await session.commit()
    inv2 = await InvoiceRepository(session).get_with_children(invoice_id)
    assert inv2
    return await invoice_to_dict_async(session, inv2, include_lines=True, include_payments=True)


@router.post("/{invoice_id}/mark-viewed")
async def mark_viewed_route(
    invoice_id: str,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(invoice_actor_auth_user_id),
):
    inv = await InvoiceRepository(session).get_with_children(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    inv = await mark_viewed(session, inv, actor_auth_user_id=actor)
    await session.commit()
    inv2 = await InvoiceRepository(session).get_with_children(invoice_id)
    assert inv2
    return await invoice_to_dict_async(session, inv2, include_lines=True, include_payments=True)


@router.post("/{invoice_id}/payments")
async def add_payment_route(
    invoice_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(invoice_actor_auth_user_id),
):
    raw = await request.body()
    if not raw.strip():
        payload: dict[str, Any] = {}
    else:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Некорректный JSON тела запроса") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Тело запроса должно быть JSON-объектом")
    body = InvoicePaymentBody.model_validate(payload)
    inv = await InvoiceRepository(session).get_with_children(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    inv = await register_payment(
        session,
        inv,
        actor_auth_user_id=actor,
        amount=body.amount,
        paid_at=body.paid_at,
        payment_method=body.payment_method,
        note=body.note,
    )
    await session.commit()
    session.expire_all()
    inv2 = await InvoiceRepository(session).get_with_children(invoice_id)
    assert inv2
    payload = await invoice_to_dict_async(session, inv2, include_lines=True, include_payments=True)
    return _invoice_json_response(payload)


@router.post("/{invoice_id}/payment-confirmation")
async def record_payment_confirmation_route(
    invoice_id: str,
    body: InvoicePaymentConfirmationBody,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(invoice_actor_auth_user_id),
):
    repo = InvoiceRepository(session)
    inv = await repo.get_with_children(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    await repo.reconcile_paid_fields(inv)
    inv = await record_payment_confirmation_document(
        session,
        inv,
        actor_auth_user_id=actor,
        document_url=body.document_url,
    )
    await session.commit()
    session.expire_all()
    inv2 = await repo.get_with_children(invoice_id)
    assert inv2
    payload = await invoice_to_dict_async(session, inv2, include_lines=True, include_payments=True)
    return _invoice_json_response(payload)


@router.post("/{invoice_id}/cancel")
async def cancel_invoice_route(
    invoice_id: str,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(invoice_actor_auth_user_id),
):
    inv = await InvoiceRepository(session).get_with_children(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    inv = await cancel_invoice(session, inv, actor_auth_user_id=actor)
    await session.commit()
    inv2 = await InvoiceRepository(session).get_with_children(invoice_id)
    assert inv2
    return await invoice_to_dict_async(session, inv2, include_lines=True, include_payments=True)


@router.delete("/{invoice_id}", status_code=204)
async def delete_draft_route(
    invoice_id: str,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(invoice_actor_auth_user_id),
):
    inv = await InvoiceRepository(session).get(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    await delete_draft_invoice(session, inv, actor_auth_user_id=actor)
    await session.commit()
    return None
