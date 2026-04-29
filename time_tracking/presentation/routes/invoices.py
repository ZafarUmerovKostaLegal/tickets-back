

from __future__ import annotations

import json
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from application.invoice_service import (
    cancel_invoice,
    create_invoice,
    delete_draft_invoice,
    get_invoices_aggregated_stats,
    invoice_to_dict,
    list_unbilled_expenses,
    list_unbilled_time_entries,
    mark_viewed,
    patch_invoice_draft,
    register_payment,
    send_invoice,
)
from infrastructure.database import get_session
from infrastructure.repository_invoices import InvoiceRepository
from presentation.schemas_invoices import InvoiceCreateBody, InvoicePatchBody, InvoicePaymentBody

router = APIRouter(prefix="/invoices", tags=["invoices"])


def _actor(actor_auth_user_id: int = Query(..., alias="actorAuthUserId")) -> int:
    if actor_auth_user_id < 0:
        raise HTTPException(status_code=400, detail="actorAuthUserId")
    return actor_auth_user_id


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

    return await get_invoices_aggregated_stats(
        session,
        client_id=client_id,
        project_id=project_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )


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
):
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
    return payload


@router.post("", status_code=201)
async def create_invoice_route(
    body: InvoiceCreateBody,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(_actor),
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
    )
    await session.commit()
    inv2 = await InvoiceRepository(session).get_with_children(inv.id)
    assert inv2
    return invoice_to_dict(inv2, include_lines=True, include_payments=True)


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
    return invoice_to_dict(inv, include_lines=True, include_payments=include_payments)


@router.patch("/{invoice_id}")
async def patch_invoice_route(
    invoice_id: str,
    body: InvoicePatchBody,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(_actor),
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
    return invoice_to_dict(inv2, include_lines=True, include_payments=True)


@router.post("/{invoice_id}/send")
async def send_invoice_route(
    invoice_id: str,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(_actor),
):
    inv = await InvoiceRepository(session).get_with_children(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    inv = await send_invoice(session, inv, actor_auth_user_id=actor)
    await session.commit()
    inv2 = await InvoiceRepository(session).get_with_children(invoice_id)
    assert inv2
    return invoice_to_dict(inv2, include_lines=True, include_payments=True)


@router.post("/{invoice_id}/mark-viewed")
async def mark_viewed_route(
    invoice_id: str,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(_actor),
):
    inv = await InvoiceRepository(session).get_with_children(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    inv = await mark_viewed(session, inv, actor_auth_user_id=actor)
    await session.commit()
    inv2 = await InvoiceRepository(session).get_with_children(invoice_id)
    assert inv2
    return invoice_to_dict(inv2, include_lines=True, include_payments=True)


@router.post("/{invoice_id}/payments")
async def add_payment_route(
    invoice_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(_actor),
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
    inv2 = await InvoiceRepository(session).get_with_children(invoice_id)
    assert inv2
    return invoice_to_dict(inv2, include_lines=True, include_payments=True)


@router.post("/{invoice_id}/cancel")
async def cancel_invoice_route(
    invoice_id: str,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(_actor),
):
    inv = await InvoiceRepository(session).get_with_children(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    inv = await cancel_invoice(session, inv, actor_auth_user_id=actor)
    await session.commit()
    inv2 = await InvoiceRepository(session).get_with_children(invoice_id)
    assert inv2
    return invoice_to_dict(inv2, include_lines=True, include_payments=True)


@router.delete("/{invoice_id}", status_code=204)
async def delete_draft_route(
    invoice_id: str,
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(_actor),
):
    inv = await InvoiceRepository(session).get(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    await delete_draft_invoice(session, inv, actor_auth_user_id=actor)
    await session.commit()
    return None
