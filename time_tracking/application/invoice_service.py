

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from application.entry_pricing import _billable_amount_for_entry
from application.report_builder import (
    _fetch_expense_report_data,
    _load_projects_map,
    filter_expense_rows_to_tt_projects,
    _load_user_rates,
)
from application.report_builder import _d as dec
from infrastructure.models import (
    TimeEntryModel,
    TimeManagerClientModel,
    TimeManagerClientProjectModel,
)
from infrastructure.models_invoices import (
    InvoiceAuditLogModel,
    InvoiceLineItemModel,
    InvoiceModel,
    InvoicePaymentModel,
)
from infrastructure.repositories import ClientProjectRepository
from infrastructure.repository_invoices import InvoiceRepository
from infrastructure.repository_shared import _now_utc


_INVOICABLE_EXPENSE_STATUSES = frozenset({"approved", "paid", "closed"})

_Q4 = Decimal("0.0001")

def _money4(v: Decimal) -> Decimal:
    return v.quantize(_Q4, rounding=ROUND_HALF_UP)


def _compute_totals(
    subtotal_lines: Decimal,
    discount_percent: Decimal | None,
    tax_percent: Decimal | None,
    tax2_percent: Decimal | None,
) -> tuple[Decimal, Decimal, Decimal]:

    sub = _money4(subtotal_lines)
    dp = discount_percent or Decimal(0)
    t1 = tax_percent or Decimal(0)
    t2 = tax2_percent or Decimal(0)
    disc = _money4(sub * dp / Decimal(100))
    after = _money4(sub - disc)
    tax_amt = _money4(after * t1 / Decimal(100) + after * t2 / Decimal(100))
    total = _money4(after + tax_amt)
    return disc, tax_amt, total


def effective_invoice_status(inv: InvoiceModel, *, today: date | None = None) -> str:

    today = today or date.today()
    if inv.status == "canceled":
        return "canceled"
    bal = _money4(inv.total_amount - inv.amount_paid)
    if inv.total_amount > 0 and bal <= 0:
        return "paid"
    if inv.status == "paid":
        return "paid"
    if _money4(inv.amount_paid) > 0 and bal > 0:
        base = "partial_paid"
    else:
        base = inv.status
    if base in ("sent", "viewed", "partial_paid") and inv.due_date < today and bal > 0:
        return "overdue"
    return base


def _require_draft(inv: InvoiceModel) -> None:
    if inv.status != "draft":
        raise HTTPException(status_code=400, detail="Изменять можно только счёт в статусе draft")


def _require_not_canceled(inv: InvoiceModel) -> None:
    if inv.status == "canceled":
        raise HTTPException(status_code=400, detail="Счёт отменён")


async def _audit(
    session: AsyncSession,
    repo: InvoiceRepository,
    invoice_id: str,
    action: str,
    actor_id: int,
    detail: dict | None = None,
) -> None:
    await repo.add_audit(
        InvoiceAuditLogModel(
            invoice_id=invoice_id,
            action=action,
            detail=json.dumps(detail, ensure_ascii=False, default=str) if detail else None,
            actor_auth_user_id=actor_id,
            created_at=_now_utc(),
        )
    )


async def _recalc_invoice_from_lines(session: AsyncSession, inv: InvoiceModel) -> None:
    await session.refresh(inv, ["line_items"])
    lines = sorted(inv.line_items, key=lambda x: (x.sort_order, x.id))
    subtotal = _money4(sum(_money4(x.line_total) for x in lines))
    disc_amt, tax_amt, total = _compute_totals(
        subtotal, inv.discount_percent, inv.tax_percent, inv.tax2_percent,
    )
    inv.subtotal = subtotal
    inv.discount_amount = disc_amt
    inv.tax_amount = tax_amt
    inv.total_amount = total
    inv.updated_at = _now_utc()
    paid = await InvoiceRepository(session).sum_payments(inv.id)
    inv.amount_paid = _money4(paid)
    _sync_payment_status(inv)


def _sync_payment_status(inv: InvoiceModel) -> None:
    if inv.status == "canceled" or inv.status == "draft":
        return
    bal = _money4(inv.total_amount - inv.amount_paid)
    if inv.total_amount > 0 and bal <= 0:
        inv.status = "paid"
    elif inv.amount_paid > 0:
        inv.status = "partial_paid"


async def create_invoice(
    session: AsyncSession,
    *,
    actor_auth_user_id: int,
    client_id: str,
    project_id: str | None,
    issue_date: date,
    due_date: date,
    currency: str | None,
    tax_percent: Decimal | None,
    tax2_percent: Decimal | None,
    discount_percent: Decimal | None,
    client_note: str | None,
    internal_note: str | None,
    lines: list[dict[str, Any]] | None,
    time_entry_ids: list[str] | None,
    expense_ids: list[str] | None,
) -> InvoiceModel:
    repo = InvoiceRepository(session)
    client = await session.get(TimeManagerClientModel, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    if project_id:
        proj = await session.get(TimeManagerClientProjectModel, project_id)
        if not proj or proj.client_id != client_id:
            raise HTTPException(status_code=400, detail="Проект не принадлежит клиенту")
    cur = (currency or client.currency or "USD").strip()[:10] or "USD"
    tp = tax_percent if tax_percent is not None else client.tax_percent
    t2p = tax2_percent if tax2_percent is not None else client.tax2_percent
    dp = discount_percent if discount_percent is not None else client.discount_percent

    year = issue_date.year
    seq = await repo.allocate_next_seq(year)
    number = f"INV-{year}-{seq:05d}"
    iid = str(uuid.uuid4())
    now = _now_utc()
    inv = InvoiceModel(
        id=iid,
        client_id=client_id,
        project_id=project_id,
        invoice_number=number,
        issue_date=issue_date,
        due_date=due_date,
        currency=cur,
        status="draft",
        subtotal=Decimal(0),
        discount_percent=dp,
        tax_percent=tp,
        tax2_percent=t2p,
        discount_amount=Decimal(0),
        tax_amount=Decimal(0),
        total_amount=Decimal(0),
        amount_paid=Decimal(0),
        client_note=client_note,
        internal_note=internal_note,
        created_by_auth_user_id=actor_auth_user_id,
        created_at=now,
        updated_at=now,
    )
    repo.add(inv)
    await session.flush()

    sort_order = 0
    if time_entry_ids:
        for tid in time_entry_ids:
            await _append_time_line(session, repo, inv, tid, sort_order, actor_auth_user_id)
            sort_order += 1
        await session.flush()
    if expense_ids:
        pid_for_exp = project_id or inv.project_id
        if not pid_for_exp:
            raise HTTPException(
                status_code=400,
                detail="Укажите projectId или добавьте сначала строки времени по проекту",
            )
        exp_rows = await _load_expense_rows_for_project(session, pid_for_exp, expense_ids)
        for eid in expense_ids:
            row = exp_rows.get(eid)
            if not row:
                raise HTTPException(status_code=400, detail=f"Расход {eid} не найден или не проходит фильтр")
            await _append_expense_line(session, repo, inv, row, sort_order, actor_auth_user_id)
            sort_order += 1
    if lines:
        for spec in lines:
            await _append_manual_line(session, repo, inv, spec, sort_order, actor_auth_user_id)
            sort_order += 1

    await session.flush()
    await _recalc_invoice_from_lines(session, inv)
    await _audit(session, repo, iid, "created", actor_auth_user_id, {"invoiceNumber": number})
    return inv


async def _append_time_line(
    session: AsyncSession,
    repo: InvoiceRepository,
    inv: InvoiceModel,
    time_entry_id: str,
    sort_order: int,
    actor_id: int,
) -> None:
    other = await repo.time_entry_on_active_invoice(time_entry_id, exclude_invoice_id=inv.id)
    if other:
        raise HTTPException(
            status_code=400,
            detail=f"Запись времени уже в счёте {other}",
        )
    entry = (
        await session.execute(select(TimeEntryModel).where(TimeEntryModel.id == time_entry_id))
    ).scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Запись времени не найдена")
    if not entry.is_billable:
        raise HTTPException(status_code=400, detail="В счёт можно включать только billable-записи")
    if inv.project_id and entry.project_id and entry.project_id != inv.project_id:
        raise HTTPException(status_code=400, detail="Запись относится к другому проекту")
    if inv.project_id is None and entry.project_id:
        inv.project_id = entry.project_id
        await session.flush()
    rates = await _load_user_rates(session, None)
    user_rates = rates.get(entry.auth_user_id)
    cpr = ClientProjectRepository(session)
    proj = await cpr.get_by_id_global(entry.project_id) if entry.project_id else None
    pc = (getattr(proj, "currency", None) or "USD") if proj else "USD"

    qty = dec(entry.hours)
    amt, _cur = _billable_amount_for_entry(
        qty,
        entry.is_billable,
        entry.work_date,
        user_rates,
        project_currency=pc,
        time_entry_project_id=entry.project_id,
    )
    line_total = _money4(amt)
    unit = _money4(line_total / qty) if qty > 0 else Decimal(0)
    desc = (entry.description or "").strip() or f"Время {entry.work_date.isoformat()}"
    repo.add_line(
        InvoiceLineItemModel(
            id=str(uuid.uuid4()),
            invoice_id=inv.id,
            sort_order=sort_order,
            line_kind="time",
            description=desc[:2000],
            quantity=qty,
            unit_amount=unit,
            line_total=line_total,
            time_entry_id=time_entry_id,
            expense_request_id=None,
        )
    )


async def _append_expense_line(
    session: AsyncSession,
    repo: InvoiceRepository,
    inv: InvoiceModel,
    row: dict[str, Any],
    sort_order: int,
    actor_id: int,
) -> None:
    eid = str(row["id"])
    other = await repo.expense_on_active_invoice(eid, exclude_invoice_id=inv.id)
    if other:
        raise HTTPException(status_code=400, detail=f"Расход уже в счёте {other}")
    st = (row.get("status") or "").strip()
    if st not in _INVOICABLE_EXPENSE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="В счёт можно включать только расходы в статусе approved, paid или closed "
            f"(сейчас: {st or '—'})",
        )
    reimb = row.get("is_reimbursable")
    if not reimb:
        raise HTTPException(status_code=400, detail="В счёт можно включать только reimbursable-расходы")
    pid = row.get("project_id")
    if pid and not inv.project_id:
        inv.project_id = str(pid)
        await session.flush()
    if inv.project_id and pid and str(pid) != str(inv.project_id):
        raise HTTPException(status_code=400, detail="Расход привязан к другому проекту")
    line_total = _money4(Decimal(str(row.get("equivalent_amount", 0))))
    desc = str(row.get("description") or "Расход")[:2000]
    repo.add_line(
        InvoiceLineItemModel(
            id=str(uuid.uuid4()),
            invoice_id=inv.id,
            sort_order=sort_order,
            line_kind="expense",
            description=desc,
            quantity=Decimal(1),
            unit_amount=line_total,
            line_total=line_total,
            time_entry_id=None,
            expense_request_id=eid,
        )
    )


async def _append_manual_line(
    session: AsyncSession,
    repo: InvoiceRepository,
    inv: InvoiceModel,
    spec: dict[str, Any],
    sort_order: int,
    actor_id: int,
) -> None:
    desc = str(spec.get("description") or "").strip()
    if not desc:
        raise HTTPException(status_code=400, detail="У строки нужно описание")
    qty = _money4(Decimal(str(spec.get("quantity", 1))))
    unit = _money4(Decimal(str(spec.get("unitAmount", spec.get("unit_amount", 0)))))
    lt = spec.get("lineTotal", spec.get("line_total"))
    if lt is not None:
        line_total = _money4(Decimal(str(lt)))
    else:
        line_total = _money4(qty * unit)
    repo.add_line(
        InvoiceLineItemModel(
            id=str(uuid.uuid4()),
            invoice_id=inv.id,
            sort_order=sort_order,
            line_kind="manual",
            description=desc[:2000],
            quantity=qty,
            unit_amount=unit,
            line_total=line_total,
            time_entry_id=None,
            expense_request_id=None,
        )
    )


async def _load_expense_rows_for_project(
    session: AsyncSession, project_id: str | None, expense_ids: list[str]
) -> dict[str, dict]:
    if not project_id or not expense_ids:
        return {}
    df = date(2000, 1, 1)
    dt = date(2099, 12, 31)
    rows = await _fetch_expense_report_data(df, dt, None, [project_id])
    pmap = await _load_projects_map(session)
    rows = filter_expense_rows_to_tt_projects(rows, pmap)
    by_id = {str(r["id"]): r for r in rows if r.get("id")}
    return {eid: by_id[eid] for eid in expense_ids if eid in by_id}


async def patch_invoice_draft(
    session: AsyncSession,
    inv: InvoiceModel,
    *,
    actor_auth_user_id: int,
    issue_date: date | None = None,
    due_date: date | None = None,
    client_note: str | None = None,
    internal_note: str | None = None,
    tax_percent: Decimal | None = None,
    tax2_percent: Decimal | None = None,
    discount_percent: Decimal | None = None,
    project_id: str | None = None,
    replace_lines: list[dict[str, Any]] | None = None,
) -> InvoiceModel:
    _require_draft(inv)
    repo = InvoiceRepository(session)
    if issue_date:
        inv.issue_date = issue_date
    if due_date:
        inv.due_date = due_date
    if client_note is not None:
        inv.client_note = client_note
    if internal_note is not None:
        inv.internal_note = internal_note
    if tax_percent is not None:
        inv.tax_percent = tax_percent
    if tax2_percent is not None:
        inv.tax2_percent = tax2_percent
    if discount_percent is not None:
        inv.discount_percent = discount_percent
    if project_id is not None:
        if project_id:
            proj = await session.get(TimeManagerClientProjectModel, project_id)
            if not proj or proj.client_id != inv.client_id:
                raise HTTPException(status_code=400, detail="Проект не принадлежит клиенту счёта")
        inv.project_id = project_id or None

    if replace_lines is not None:
        await repo.delete_lines(inv.id)
        await session.flush()
        for idx, spec in enumerate(replace_lines):
            kind = (spec.get("lineKind") or spec.get("line_kind") or "manual").lower()
            if kind == "time":
                tid = spec.get("timeEntryId") or spec.get("time_entry_id")
                if not tid:
                    raise HTTPException(status_code=400, detail="timeEntryId обязателен для строки time")
                await _append_time_line(session, repo, inv, str(tid), idx, actor_auth_user_id)
            elif kind == "expense":
                eid = spec.get("expenseRequestId") or spec.get("expense_request_id")
                if not eid or not inv.project_id:
                    raise HTTPException(status_code=400, detail="expenseRequestId и projectId обязательны")
                rows = await _load_expense_rows_for_project(session, inv.project_id, [str(eid)])
                row = rows.get(str(eid))
                if not row:
                    raise HTTPException(status_code=400, detail="Расход не найден")
                await _append_expense_line(session, repo, inv, row, idx, actor_auth_user_id)
            else:
                await _append_manual_line(session, repo, inv, spec, idx, actor_auth_user_id)
        await session.flush()

    await _recalc_invoice_from_lines(session, inv)
    await _audit(session, repo, inv.id, "updated", actor_auth_user_id, {})
    return inv


async def send_invoice(session: AsyncSession, inv: InvoiceModel, *, actor_auth_user_id: int) -> InvoiceModel:
    if inv.status == "canceled":
        raise HTTPException(status_code=400, detail="Нельзя отправить отменённый счёт")
    if inv.status == "draft":
        inv.status = "sent"
    now = _now_utc()
    if inv.sent_at is None:
        inv.sent_at = now
    inv.last_sent_at = now
    inv.updated_at = now
    repo = InvoiceRepository(session)
    await _audit(session, repo, inv.id, "sent", actor_auth_user_id, {})
    return inv


async def mark_viewed(session: AsyncSession, inv: InvoiceModel, *, actor_auth_user_id: int) -> InvoiceModel:
    _require_not_canceled(inv)
    if inv.status == "draft":
        raise HTTPException(status_code=400, detail="Сначала отправьте счёт")
    now = _now_utc()
    inv.viewed_at = now
    if inv.status == "sent":
        inv.status = "viewed"
    inv.updated_at = now
    repo = InvoiceRepository(session)
    await _audit(session, repo, inv.id, "viewed", actor_auth_user_id, {})
    return inv


async def register_payment(
    session: AsyncSession,
    inv: InvoiceModel,
    *,
    actor_auth_user_id: int,
    amount: Decimal | None,
    paid_at: datetime | None,
    payment_method: str | None,
    note: str | None,
) -> InvoiceModel:
    if inv.status == "canceled":
        raise HTTPException(status_code=400, detail="Нельзя принять оплату по отменённому счёту")
    if inv.status == "draft":
        raise HTTPException(status_code=400, detail="Сначала отправьте счёт")
    remaining = _money4(inv.total_amount - inv.amount_paid)
    if amount is None:
        amt = remaining
    else:
        amt = _money4(amount)
    if amt <= 0:
        if remaining <= 0:
            raise HTTPException(status_code=400, detail="Счёт уже полностью оплачен")
        raise HTTPException(status_code=400, detail="Сумма оплаты должна быть больше нуля")
    when = paid_at if paid_at is not None else _now_utc()
    repo = InvoiceRepository(session)
    pid = str(uuid.uuid4())
    repo.add_payment(
        InvoicePaymentModel(
            id=pid,
            invoice_id=inv.id,
            amount=amt,
            payment_method=(payment_method or "")[:64] or None,
            note=note,
            recorded_by_auth_user_id=actor_auth_user_id,
            paid_at=when if when.tzinfo else when.replace(tzinfo=timezone.utc),
            created_at=_now_utc(),
        )
    )
    await session.flush()
    inv.amount_paid = await repo.sum_payments(inv.id)
    _sync_payment_status(inv)
    flag_modified(inv, "amount_paid")
    flag_modified(inv, "status")
    inv.updated_at = _now_utc()
    await _audit(
        session,
        repo,
        inv.id,
        "payment_registered",
        actor_auth_user_id,
        {"amount": str(amt), "paymentId": pid},
    )
    return inv


async def cancel_invoice(session: AsyncSession, inv: InvoiceModel, *, actor_auth_user_id: int) -> InvoiceModel:
    if inv.status == "canceled":
        return inv
    inv.status = "canceled"
    inv.canceled_at = _now_utc()
    inv.updated_at = _now_utc()
    repo = InvoiceRepository(session)
    await _audit(session, repo, inv.id, "canceled", actor_auth_user_id, {})
    return inv


async def delete_draft_invoice(
    session: AsyncSession, inv: InvoiceModel, *, actor_auth_user_id: int,
) -> None:
    _require_draft(inv)
    repo = InvoiceRepository(session)
    if await repo.sum_payments(inv.id) > 0:
        raise HTTPException(status_code=400, detail="Нельзя удалить счёт с платежами")
    _ = actor_auth_user_id
    await session.delete(inv)


def invoice_to_dict(inv: InvoiceModel, *, include_lines: bool = True, include_payments: bool = False) -> dict:
    eff = effective_invoice_status(inv)
    out: dict[str, Any] = {
        "id": inv.id,
        "clientId": inv.client_id,
        "projectId": inv.project_id,
        "invoiceNumber": inv.invoice_number,
        "issueDate": inv.issue_date.isoformat(),
        "dueDate": inv.due_date.isoformat(),
        "currency": inv.currency,
        "status": eff,
        "storedStatus": inv.status,
        "subtotal": float(inv.subtotal),
        "discountPercent": float(inv.discount_percent) if inv.discount_percent is not None else None,
        "taxPercent": float(inv.tax_percent) if inv.tax_percent is not None else None,
        "tax2Percent": float(inv.tax2_percent) if inv.tax2_percent is not None else None,
        "discountAmount": float(inv.discount_amount),
        "taxAmount": float(inv.tax_amount),
        "totalAmount": float(inv.total_amount),
        "amountPaid": float(inv.amount_paid),
        "balanceDue": float(_money4(inv.total_amount - inv.amount_paid)),
        "clientNote": inv.client_note,
        "internalNote": inv.internal_note,
        "sentAt": inv.sent_at.isoformat() if inv.sent_at else None,
        "lastSentAt": inv.last_sent_at.isoformat() if inv.last_sent_at else None,
        "viewedAt": inv.viewed_at.isoformat() if inv.viewed_at else None,
        "canceledAt": inv.canceled_at.isoformat() if inv.canceled_at else None,
        "createdByAuthUserId": inv.created_by_auth_user_id,
        "createdAt": inv.created_at.isoformat(),
        "updatedAt": inv.updated_at.isoformat() if inv.updated_at else None,
    }
    if include_lines:
        lines = sorted(inv.line_items, key=lambda x: (x.sort_order, x.id))
        out["lines"] = [
            {
                "id": li.id,
                "sortOrder": li.sort_order,
                "lineKind": li.line_kind,
                "description": li.description,
                "quantity": float(li.quantity),
                "unitAmount": float(li.unit_amount),
                "lineTotal": float(li.line_total),
                "timeEntryId": li.time_entry_id,
                "expenseRequestId": li.expense_request_id,
            }
            for li in lines
        ]
    if include_payments:
        pays = sorted(inv.payments, key=lambda x: x.paid_at)
        out["payments"] = [
            {
                "id": p.id,
                "amount": float(p.amount),
                "paymentMethod": p.payment_method,
                "note": p.note,
                "recordedByAuthUserId": p.recorded_by_auth_user_id,
                "paidAt": p.paid_at.isoformat(),
                "createdAt": p.created_at.isoformat(),
            }
            for p in pays
        ]
    return out


async def get_invoices_aggregated_stats(
    session: AsyncSession,
    *,
    client_id: str | None = None,
    project_id: str | None = None,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:

    repo = InvoiceRepository(session)
    rows = await repo.list_invoices_for_aggregation(
        client_id=client_id,
        project_id=project_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    by_eff: dict[str, int] = {}
    by_cur: dict[str, dict[str, Decimal]] = {}
    total_amount_all = Decimal(0)
    amount_paid_all = Decimal(0)
    for inv in rows:
        eff = effective_invoice_status(inv)
        by_eff[eff] = by_eff.get(eff, 0) + 1
        cur = (inv.currency or "USD").strip().upper()[:10] or "USD"
        b = by_cur.setdefault(
            cur,
            {"count": 0, "totalAmount": Decimal(0), "amountPaid": Decimal(0)},
        )
        b["count"] += 1
        b["totalAmount"] += _money4(inv.total_amount)
        b["amountPaid"] += _money4(inv.amount_paid)
        total_amount_all += _money4(inv.total_amount)
        amount_paid_all += _money4(inv.amount_paid)
    unpaid_count = 0
    open_balance = Decimal(0)
    for inv in rows:
        bal = _money4(inv.total_amount - inv.amount_paid)
        if inv.status in ("canceled", "draft"):
            continue
        if bal > 0:
            unpaid_count += 1
            open_balance += bal
    by_currency_out: dict[str, Any] = {}
    for c, v in sorted(by_cur.items()):
        t = v["totalAmount"]
        p = v["amountPaid"]
        by_currency_out[c] = {
            "count": v["count"],
            "totalAmount": float(_money4(t)),
            "amountPaid": float(_money4(p)),
            "balanceDue": float(_money4(t - p)),
        }
    return {
        "totalInvoices": len(rows),
        "byEffectiveStatus": by_eff,
        "byCurrency": by_currency_out,
        "totals": {
            "totalAmount": float(_money4(total_amount_all)),
            "amountPaid": float(_money4(amount_paid_all)),
            "balanceDue": float(_money4(total_amount_all - amount_paid_all)),
        },
        "unpaidInvoicesCount": unpaid_count,
        "openBalanceDue": float(_money4(open_balance)),
        "cappedAt": 50_000,
        "isCapped": len(rows) >= 50_000,
    }


async def list_unbilled_time_entries(
    session: AsyncSession,
    *,
    project_id: str,
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    repo = InvoiceRepository(session)
    q = (
        select(TimeEntryModel)
        .where(
            TimeEntryModel.project_id == project_id,
            TimeEntryModel.work_date >= date_from,
            TimeEntryModel.work_date <= date_to,
            TimeEntryModel.is_billable.is_(True),
            TimeEntryModel.voided_at.is_(None),
        )
        .order_by(TimeEntryModel.work_date, TimeEntryModel.id)
    )
    entries = list((await session.execute(q)).scalars().all())
    ids = [e.id for e in entries]
    invoiced = await repo.invoiced_time_entry_ids(ids)
    rates = await _load_user_rates(session, None)
    cpr = ClientProjectRepository(session)
    proj = await cpr.get_by_id_global(project_id)
    pc = (getattr(proj, "currency", None) or "USD") if proj else "USD"
    out: list[dict[str, Any]] = []
    for e in entries:
        if e.id in invoiced:
            continue
        h = dec(e.hours)
        amt, cur = _billable_amount_for_entry(
            h,
            e.is_billable,
            e.work_date,
            rates.get(e.auth_user_id),
            project_currency=pc,
            time_entry_project_id=e.project_id,
        )
        out.append(
            {
                "id": e.id,
                "authUserId": e.auth_user_id,
                "workDate": e.work_date.isoformat(),
                "hours": float(h),

                "roundedHours": float(h),
                "durationSeconds": int(e.duration_seconds),
                "description": e.description,
                "billableAmount": float(_money4(amt)),
                "currency": cur,
            }
        )
    return out


async def list_unbilled_expenses(
    session: AsyncSession,
    *,
    project_id: str,
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    repo = InvoiceRepository(session)
    rows = await _fetch_expense_report_data(date_from, date_to, None, [project_id])
    pmap = await _load_projects_map(session)
    rows = filter_expense_rows_to_tt_projects(rows, pmap)
    candidates = [r for r in rows if r.get("is_reimbursable") and r.get("id")]
    eids = [str(r["id"]) for r in candidates]
    invoiced = await repo.invoiced_expense_ids(eids)
    out: list[dict[str, Any]] = []
    for r in candidates:
        eid = str(r["id"])
        if eid in invoiced:
            continue
        out.append(
            {
                "id": eid,
                "expenseDate": r.get("expense_date"),
                "description": r.get("description"),
                "equivalentAmount": float(r.get("equivalent_amount", 0)),
                "status": r.get("status"),
            }
        )
    return out
