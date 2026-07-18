

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy import and_, inspect as orm_inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from application.duplicate_time_entries import deduplicate_entries_for_report
from application.entry_pricing import _billable_amount_for_entry, _billable_rate_for_entry
from application.invoice_fx import FxRateBook, convert_or_same, load_fx_rate_book
from application.package_billing import (
    compute_entry_splits_for_project_entries,
    is_hour_package_project,
    month_key,
    package_fee_description,
    package_fee_x,
    package_hours_n,
)
from application.partner_confirmed_invoice_preview import (
    build_partner_confirmed_invoice_preview,
)
from application.money_amounts import money_product_hours_rate
from application.time_rounding import invoice_hours_for_billing, invoice_rate_for_billing
from application.task_billing import is_flat_fee_task
from application.partner_report_confirmation_service import (
    ensure_fully_confirmed_partner_period_or_403,
)
from application.report_builder import (
    _fetch_expense_report_data,
    _load_user_rates,
)
from application.report_builder import _d as dec
from infrastructure.models import (
    TimeEntryModel,
    TimeManagerClientModel,
    TimeManagerClientProjectModel,
    TimeManagerClientTaskModel,
)
from infrastructure.models_invoices import (
    InvoiceAuditLogModel,
    InvoiceLineItemModel,
    InvoiceModel,
    InvoicePaymentModel,
)
from infrastructure.repositories import ClientProjectRepository, ClientTaskRepository
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


def effective_invoice_status(
    inv: InvoiceModel,
    *,
    today: date | None = None,
    amount_paid_override: Decimal | None = None,
) -> str:

    today = today or date.today()
    paid = _money4(amount_paid_override) if amount_paid_override is not None else _money4(inv.amount_paid)
    if inv.status == "canceled":
        return "canceled"
    bal = _money4(inv.total_amount - paid)
    if inv.total_amount > 0 and bal <= 0:
        return "paid"
    if inv.status == "paid":
        return "paid"
    if paid > 0 and bal > 0:
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
    partner_billing_period_from: date | None = None,
    partner_billing_period_to: date | None = None,
    invoice_number: str | None = None,
    partner_confirmation_request_id: str | None = None,
) -> InvoiceModel:
    repo = InvoiceRepository(session)
    client = await session.get(TimeManagerClientModel, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    if project_id:
        proj = await session.get(TimeManagerClientProjectModel, project_id)
        if not proj or proj.client_id != client_id:
            raise HTTPException(status_code=400, detail="Проект не принадлежит клиенту")
    cur = (currency or client.currency or "USD").strip().upper()[:10] or "USD"
    fx_book = await load_fx_rate_book(session)

    needs_partner_confirmation = bool(time_entry_ids) or bool(expense_ids) or (
        bool(lines) and len(lines) > 0 and bool((project_id or "").strip())
    ) or (
        partner_billing_period_from is not None and partner_billing_period_to is not None
    )
    eff_pid = (project_id or "").strip() or None
    if needs_partner_confirmation:
        if not eff_pid and time_entry_ids:
            row = (
                await session.execute(
                    select(TimeEntryModel.project_id).where(TimeEntryModel.id == time_entry_ids[0])
                )
            ).scalar_one_or_none()
            eff_pid = str(row).strip() if row is not None else None
        if not eff_pid:
            raise HTTPException(
                status_code=400,
                detail="Укажите projectId для счёта с временем, расходами или строками по проекту",
            )
        if partner_billing_period_from is None or partner_billing_period_to is None:
            raise HTTPException(
                status_code=400,
                detail="Укажите partnerBillingPeriodFrom и partnerBillingPeriodTo "
                "(интервал биллинга должен полностью входить в уже подтверждённый партнёрами период по проекту)",
            )
        if partner_billing_period_to < partner_billing_period_from:
            raise HTTPException(status_code=400, detail="partnerBillingPeriodTo не может быть раньше partnerBillingPeriodFrom")
        await ensure_fully_confirmed_partner_period_or_403(
            session,
            project_id=eff_pid,
            date_from=partner_billing_period_from,
            date_to=partner_billing_period_to,
        )

    partner_preview = None
    if (
        partner_billing_period_from is not None
        and partner_billing_period_to is not None
        and eff_pid
    ):
        partner_preview = await build_partner_confirmed_invoice_preview(
            session,
            project_id=eff_pid,
            date_from=partner_billing_period_from,
            date_to=partner_billing_period_to,
            invoice_currency=cur,
            issue_date=issue_date,
            fx_book=fx_book,
            exclude_invoiced=True,
        )
        # Source of truth for partner-confirmed invoices: preview entry sets (deduped + package rules).
        time_entry_ids = list(partner_preview.time_entry_ids)
        expense_ids = list(partner_preview.expense_ids)
        if not time_entry_ids and not expense_ids and partner_preview.package_fee_subtotal <= 0:
            raise HTTPException(
                status_code=400,
                detail="Нет невыставленных строк по подтверждённому периоду для счёта",
            )

    tp = tax_percent if tax_percent is not None else client.tax_percent
    t2p = tax2_percent if tax2_percent is not None else client.tax2_percent
    dp = discount_percent if discount_percent is not None else client.discount_percent
    # Partner-confirmed invoices match report totals (pre-tax) unless caller sets tax explicitly.
    if partner_preview is not None and tax_percent is None and tax2_percent is None and discount_percent is None:
        tp = Decimal(0)
        t2p = Decimal(0)
        dp = Decimal(0)

    manual_number = (invoice_number or "").strip()
    if manual_number:
        if len(manual_number) > 64:
            raise HTTPException(status_code=400, detail="Номер счёта слишком длинный (макс. 64 символа)")
        if await repo.exists_invoice_number(manual_number):
            raise HTTPException(
                status_code=409,
                detail=f"Счёт с номером «{manual_number}» уже существует",
            )
        number = manual_number
    else:
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
        partner_billing_period_from=partner_billing_period_from,
        partner_billing_period_to=partner_billing_period_to,
        partner_confirmation_request_id=(partner_confirmation_request_id or "").strip() or None,
    )
    repo.add(inv)
    await session.flush()

    sort_order = 0
    package_months: set[tuple[str, int, int]] = set()
    if partner_preview is not None:
        # Materialize exact preview lines (already minute+2dp hours, Excel-aligned money).
        for ln in partner_preview.lines:
            if ln.time_entry_id:
                other = await repo.time_entry_on_active_invoice(ln.time_entry_id, exclude_invoice_id=inv.id)
                if other:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Запись времени уже в счёте {other}",
                    )
            if ln.expense_request_id:
                other = await repo.expense_on_active_invoice(
                    ln.expense_request_id, exclude_invoice_id=inv.id
                )
                if other:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Расход уже в счёте {other}",
                    )
            repo.add_line(
                InvoiceLineItemModel(
                    id=str(uuid.uuid4()),
                    invoice_id=inv.id,
                    sort_order=sort_order,
                    line_kind=ln.line_kind,
                    description=(ln.description or "")[:2000],
                    quantity=ln.quantity,
                    unit_amount=ln.unit_amount,
                    line_total=ln.line_total,
                    time_entry_id=ln.time_entry_id,
                    expense_request_id=ln.expense_request_id,
                    source_currency=ln.source_currency,
                    source_amount=ln.source_amount,
                    fx_rate=ln.fx_rate,
                )
            )
            sort_order += 1
        if not inv.project_id and eff_pid:
            inv.project_id = eff_pid
        await session.flush()
    else:
        if time_entry_ids:
            for tid in time_entry_ids:
                added, month_hit = await _append_time_line(
                    session, repo, inv, tid, sort_order, actor_auth_user_id, fx_book=fx_book,
                )
                if added:
                    sort_order += 1
                if month_hit:
                    package_months.add(month_hit)
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
                await _append_expense_line(
                    session, repo, inv, row, sort_order, actor_auth_user_id, fx_book=fx_book,
                )
                sort_order += 1
        if lines:
            for spec in lines:
                await _append_manual_line(session, repo, inv, spec, sort_order, actor_auth_user_id)
                sort_order += 1

        if partner_billing_period_from and partner_billing_period_to and (project_id or inv.project_id):
            pid = (project_id or inv.project_id or "").strip()
            if pid:
                y, m = partner_billing_period_from.year, partner_billing_period_from.month
                ey, em = partner_billing_period_to.year, partner_billing_period_to.month
                while (y, m) <= (ey, em):
                    package_months.add((pid, y, m))
                    if m == 12:
                        y, m = y + 1, 1
                    else:
                        m += 1

        sort_order = await _ensure_package_fee_lines(
            session, repo, inv, package_months, sort_order, fx_book=fx_book,
        )

    await session.flush()
    await _recalc_invoice_from_lines(session, inv)

    if partner_preview is not None:
        expected = _money4(partner_preview.expected_subtotal)
        actual = _money4(inv.subtotal)
        if abs(actual - expected) > Decimal("0.01"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "INVOICE_SUBTOTAL_MISMATCH",
                    "message": (
                        "Сумма счёта не совпала с подтверждённым отчётом "
                        f"(ожидалось {float(expected)} {cur}, получилось {float(actual)} {cur})"
                    ),
                    "expectedSubtotal": float(expected),
                    "actualSubtotal": float(actual),
                    "currency": cur,
                    "preview": partner_preview.as_dict(),
                },
            )

    await _audit(session, repo, iid, "created", actor_auth_user_id, {"invoiceNumber": number})
    return inv


def _package_fee_marker(project_id: str, year: int, month: int) -> str:
    return f"[package_fee:{project_id}:{year:04d}-{month:02d}]"


async def _load_project_billable_entries(
    session: AsyncSession, project_id: str
) -> list[TimeEntryModel]:
    q = select(TimeEntryModel).where(
        TimeEntryModel.project_id == project_id,
        TimeEntryModel.voided_at.is_(None),
        TimeEntryModel.is_billable.is_(True),
    )
    return list((await session.execute(q)).scalars().all())


async def _ensure_package_fee_lines(
    session: AsyncSession,
    repo: InvoiceRepository,
    inv: InvoiceModel,
    package_months: set[tuple[str, int, int]],
    sort_order: int,
    *,
    fx_book: FxRateBook | None = None,
) -> int:
    if not package_months:
        return sort_order
    # Must eager-load under AsyncSession — lazy inv.line_items → greenlet_spawn (xd2s).
    await session.refresh(inv, ["line_items"])
    line_items = list(inv.line_items or [])
    existing = {
        (li.description or "")
        for li in line_items
        if (li.line_kind or "") == "package_fee"
    }
    book = fx_book or await load_fx_rate_book(session)
    inv_ccy = (inv.currency or "USD").strip().upper()[:10] or "USD"
    cpr = ClientProjectRepository(session)
    for pid, y, m in sorted(package_months):
        proj = await cpr.get_by_id_global(pid)
        if not proj or not is_hour_package_project(proj):
            continue
        fee = package_fee_x(proj)
        n = package_hours_n(proj)
        if fee <= 0:
            continue
        marker = _package_fee_marker(pid, y, m)
        if any(marker in d for d in existing):
            continue
        if any(marker in (li.description or "") for li in line_items):
            continue
        desc = f"{marker} {package_fee_description(proj.name or pid, y, m, n)}"
        project_ccy = (getattr(proj, "currency", None) or "USD").strip().upper()[:10] or "USD"
        conv = convert_or_same(book, _money4(fee), project_ccy, inv_ccy, inv.issue_date)
        repo.add_line(
            InvoiceLineItemModel(
                id=str(uuid.uuid4()),
                invoice_id=inv.id,
                sort_order=sort_order,
                line_kind="package_fee",
                description=desc[:2000],
                quantity=Decimal(1),
                unit_amount=conv.converted_amount,
                line_total=conv.converted_amount,
                time_entry_id=None,
                expense_request_id=None,
                source_currency=conv.source_currency,
                source_amount=conv.source_amount,
                fx_rate=conv.fx_rate,
            )
        )
        existing.add(desc)
        sort_order += 1
    return sort_order


async def _append_time_line(
    session: AsyncSession,
    repo: InvoiceRepository,
    inv: InvoiceModel,
    time_entry_id: str,
    sort_order: int,
    actor_id: int,
    *,
    fx_book: FxRateBook | None = None,
) -> tuple[bool, tuple[str, int, int] | None]:
    """Returns (line_added, optional (project_id, year, month) for package fee)."""
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
    inv_ccy = (inv.currency or "USD").strip().upper()[:10] or "USD"
    book = fx_book or await load_fx_rate_book(session)

    task = None
    if entry.task_id and entry.project_id:
        task = await ClientTaskRepository(session).get_by_id(str(entry.project_id), str(entry.task_id))

    month_hit: tuple[str, int, int] | None = None
    if is_flat_fee_task(task):
        if entry.project_id and proj and is_hour_package_project(proj):
            y, m = month_key(entry.work_date)
            month_hit = (str(entry.project_id), y, m)
        qty = Decimal(1)
        amt, _cur = _billable_amount_for_entry(
            invoice_hours_for_billing(entry.hours) or dec(entry.hours),
            entry.is_billable,
            entry.work_date,
            user_rates,
            project_currency=pc,
            time_entry_project_id=entry.project_id,
            task=task,
        )
        src_total = _money4(amt)
        unit_src = src_total
        desc = (entry.description or "").strip() or f"Время {entry.work_date.isoformat()}"
        if task and (task.name or "").strip():
            desc = f"{(task.name or '').strip()}: {desc}"
        conv = convert_or_same(book, src_total, pc, inv_ccy, inv.issue_date)
        unit_conv = convert_or_same(book, unit_src, pc, inv_ccy, inv.issue_date).converted_amount
        repo.add_line(
            InvoiceLineItemModel(
                id=str(uuid.uuid4()),
                invoice_id=inv.id,
                sort_order=sort_order,
                line_kind="time",
                description=desc[:2000],
                quantity=qty,
                unit_amount=unit_conv,
                line_total=conv.converted_amount,
                time_entry_id=time_entry_id,
                expense_request_id=None,
                source_currency=conv.source_currency,
                source_amount=conv.source_amount,
                fx_rate=conv.fx_rate,
            )
        )
        return True, month_hit

    if entry.project_id and proj and is_hour_package_project(proj):
        y, m = month_key(entry.work_date)
        month_hit = (str(entry.project_id), y, m)
        all_entries = await _load_project_billable_entries(session, str(entry.project_id))
        # Need all tasks for package exclusion of flat-fee entries on this project
        task_ids = {str(e.task_id) for e in all_entries if e.task_id}
        full_tasks: dict[str, Any] = {}
        if task_ids:
            rows = (
                await session.execute(
                    select(TimeManagerClientTaskModel).where(TimeManagerClientTaskModel.id.in_(list(task_ids)))
                )
            ).scalars().all()
            full_tasks = {str(r.id): r for r in rows}
        _, splits = compute_entry_splits_for_project_entries(proj, all_entries, tasks_map=full_tasks)
        split = splits.get(str(entry.id))
        if not split or split.overage_hours <= 0:
            # Covered by package — no time line; package fee still owed for the month.
            return False, month_hit
        qty = invoice_hours_for_billing(split.overage_hours)
        if qty <= 0:
            return False, month_hit
    else:
        qty = invoice_hours_for_billing(entry.hours)
        if qty <= 0:
            return False, None

    rate_amt, _rate_cur = _billable_rate_for_entry(
        entry.work_date,
        user_rates,
        project_currency=pc,
        time_entry_project_id=entry.project_id,
        task=task,
    )
    unit_src = invoice_rate_for_billing(rate_amt)
    if unit_src > 0:
        src_total = money_product_hours_rate(qty, unit_src)
    else:
        amt, _cur = _billable_amount_for_entry(
            qty,
            entry.is_billable,
            entry.work_date,
            user_rates,
            project_currency=pc,
            time_entry_project_id=entry.project_id,
            task=task,
        )
        src_total = _money4(amt)
        unit_src = _money4(src_total / qty) if qty > 0 else Decimal(0)
    desc = (entry.description or "").strip() or f"Время {entry.work_date.isoformat()}"
    if month_hit:
        desc = f"{desc} (overage)"
    conv = convert_or_same(book, src_total, pc, inv_ccy, inv.issue_date)
    unit_conv = convert_or_same(book, unit_src, pc, inv_ccy, inv.issue_date).converted_amount
    repo.add_line(
        InvoiceLineItemModel(
            id=str(uuid.uuid4()),
            invoice_id=inv.id,
            sort_order=sort_order,
            line_kind="time",
            description=desc[:2000],
            quantity=qty,
            unit_amount=unit_conv,
            line_total=conv.converted_amount,
            time_entry_id=time_entry_id,
            expense_request_id=None,
            source_currency=conv.source_currency,
            source_amount=conv.source_amount,
            fx_rate=conv.fx_rate,
        )
    )
    return True, month_hit


async def _append_expense_line(
    session: AsyncSession,
    repo: InvoiceRepository,
    inv: InvoiceModel,
    row: dict[str, Any],
    sort_order: int,
    actor_id: int,
    *,
    fx_book: FxRateBook | None = None,
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
    src_total = _money4(Decimal(str(row.get("equivalent_amount", 0))))
    inv_ccy = (inv.currency or "USD").strip().upper()[:10] or "USD"
    book = fx_book or await load_fx_rate_book(session)
    # Expense equivalent_amount is always USD
    conv = convert_or_same(book, src_total, "USD", inv_ccy, inv.issue_date)
    desc = str(row.get("description") or "Расход")[:2000]
    repo.add_line(
        InvoiceLineItemModel(
            id=str(uuid.uuid4()),
            invoice_id=inv.id,
            sort_order=sort_order,
            line_kind="expense",
            description=desc,
            quantity=Decimal(1),
            unit_amount=conv.converted_amount,
            line_total=conv.converted_amount,
            time_entry_id=None,
            expense_request_id=eid,
            source_currency=conv.source_currency,
            source_amount=conv.source_amount,
            fx_rate=conv.fx_rate,
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
        package_months: set[tuple[str, int, int]] = set()
        sort_idx = 0
        for spec in replace_lines:
            kind = (spec.get("lineKind") or spec.get("line_kind") or "manual").lower()
            if kind == "time":
                tid = spec.get("timeEntryId") or spec.get("time_entry_id")
                if not tid:
                    raise HTTPException(status_code=400, detail="timeEntryId обязателен для строки time")
                added, month_hit = await _append_time_line(
                    session, repo, inv, str(tid), sort_idx, actor_auth_user_id
                )
                if added:
                    sort_idx += 1
                if month_hit:
                    package_months.add(month_hit)
            elif kind == "expense":
                eid = spec.get("expenseRequestId") or spec.get("expense_request_id")
                if not eid or not inv.project_id:
                    raise HTTPException(status_code=400, detail="expenseRequestId и projectId обязательны")
                rows = await _load_expense_rows_for_project(session, inv.project_id, [str(eid)])
                row = rows.get(str(eid))
                if not row:
                    raise HTTPException(status_code=400, detail="Расход не найден")
                await _append_expense_line(session, repo, inv, row, sort_idx, actor_auth_user_id)
                sort_idx += 1
            elif kind == "package_fee":
                await _append_manual_line(session, repo, inv, {**spec, "lineKind": "manual"}, sort_idx, actor_auth_user_id)
                sort_idx += 1
            else:
                await _append_manual_line(session, repo, inv, spec, sort_idx, actor_auth_user_id)
                sort_idx += 1
        await _ensure_package_fee_lines(session, repo, inv, package_months, sort_idx)
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
    repo = InvoiceRepository(session)
    inv.amount_paid = _money4(await repo.sum_payments(inv.id))
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
    inv.amount_paid = _money4(await repo.sum_payments(inv.id))
    _sync_payment_status(inv)
    flag_modified(inv, "amount_paid")
    flag_modified(inv, "status")
    inv.updated_at = _now_utc()
    detail: dict[str, Any] = {"amount": str(amt), "paymentId": pid}
    bal_after = _money4(inv.total_amount - inv.amount_paid)
    doc_url = getattr(inv, "payment_confirmation_document_url", None) or ""
    if inv.total_amount > 0 and bal_after <= 0 and not str(doc_url).strip():
        detail["requiresPaymentConfirmationDocument"] = True
    await _audit(
        session,
        repo,
        inv.id,
        "payment_registered",
        actor_auth_user_id,
        detail,
    )
    return inv


async def record_payment_confirmation_document(
    session: AsyncSession,
    inv: InvoiceModel,
    *,
    actor_auth_user_id: int,
    document_url: str,
) -> InvoiceModel:
    if inv.status in ("canceled", "draft"):
        raise HTTPException(status_code=400, detail="Недопустимый статус счёта")
    repo = InvoiceRepository(session)
    paid_sum = _money4(await repo.sum_payments(inv.id))
    eff = effective_invoice_status(inv, amount_paid_override=paid_sum)
    if eff != "paid":
        raise HTTPException(
            status_code=400,
            detail="Документ оплаты можно прикрепить только к полностью оплаченному счёту",
        )
    url = document_url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Укажите ссылку или путь к документу")
    inv.payment_confirmation_document_url = url[:4096]
    inv.payment_confirmation_recorded_at = _now_utc()
    inv.updated_at = _now_utc()
    await _audit(
        session,
        repo,
        inv.id,
        "payment_confirmation_recorded",
        actor_auth_user_id,
        {"documentUrl": url[:500]},
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
    """Hard-delete draft or canceled invoice (no payments). Releases time/expense lines for re-billing."""
    status = (inv.status or "").strip()
    if status not in ("draft", "canceled"):
        raise HTTPException(
            status_code=400,
            detail="Удалить можно только черновик или отменённый счёт. Сначала отмените счёт.",
        )
    repo = InvoiceRepository(session)
    if await repo.sum_payments(inv.id) > 0:
        raise HTTPException(status_code=400, detail="Нельзя удалить счёт с платежами")
    _ = actor_auth_user_id
    await session.delete(inv)


async def load_time_entry_hints_for_invoice_lines(
    session: AsyncSession,
    line_items: Iterable[InvoiceLineItemModel],
) -> dict[str, tuple[date, int]]:
    """Дата работы и автор записи времени для строк счёта (§3 BACKEND_SPEC_INVOICE_DOCUMENTS)."""
    ids = [
        str(li.time_entry_id)
        for li in line_items
        if li.line_kind == "time" and li.time_entry_id
    ]
    uniq = list(dict.fromkeys(ids))
    if not uniq:
        return {}
    q = select(TimeEntryModel.id, TimeEntryModel.work_date, TimeEntryModel.auth_user_id).where(
        TimeEntryModel.id.in_(uniq)
    )
    rows = (await session.execute(q)).all()
    return {str(tid): (wd, int(uid)) for tid, wd, uid in rows}


async def invoice_to_dict_async(
    session: AsyncSession,
    inv: InvoiceModel,
    *,
    include_lines: bool = True,
    include_payments: bool = False,
) -> dict[str, Any]:
    hints: dict[str, tuple[date, int]] = {}
    to_load: list[str] = []
    insp = orm_inspect(inv)
    if include_lines and "line_items" in insp.unloaded:
        to_load.append("line_items")
    if include_payments and "payments" in insp.unloaded:
        to_load.append("payments")
    if to_load:
        await session.refresh(inv, to_load)
    if include_lines and inv.line_items:
        hints = await load_time_entry_hints_for_invoice_lines(session, inv.line_items)
    return invoice_to_dict(
        inv,
        include_lines=include_lines,
        include_payments=include_payments,
        time_entry_hints=hints,
    )


def invoice_to_dict(
    inv: InvoiceModel,
    *,
    include_lines: bool = True,
    include_payments: bool = False,
    time_entry_hints: dict[str, tuple[date, int]] | None = None,
) -> dict[str, Any]:
    amount_paid_override: Decimal | None = None
    if include_payments:
        try:
            if "payments" not in orm_inspect(inv).unloaded:
                plist = list(inv.payments or [])
                amount_paid_override = _money4(sum(_money4(p.amount) for p in plist))
        except Exception:
            amount_paid_override = None
    paid_display = (
        _money4(amount_paid_override)
        if amount_paid_override is not None
        else _money4(inv.amount_paid)
    )
    eff = effective_invoice_status(inv, amount_paid_override=amount_paid_override)
    balance_due_val = _money4(inv.total_amount - paid_display)
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
        "amountPaid": float(paid_display),
        "balanceDue": float(balance_due_val),
                                                                                                 
        "total_amount": float(inv.total_amount),
        "amount_paid": float(paid_display),
        "balance_due": float(balance_due_val),
        "effective_status": eff,
        "stored_status": inv.status,
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
    pf = getattr(inv, "partner_billing_period_from", None)
    pt = getattr(inv, "partner_billing_period_to", None)
    pcr = getattr(inv, "partner_confirmation_request_id", None)
    out["partnerBillingPeriodFrom"] = pf.isoformat() if pf else None
    out["partnerBillingPeriodTo"] = pt.isoformat() if pt else None
    out["partnerConfirmationRequestId"] = (str(pcr).strip() or None) if pcr else None
    out["partner_billing_period_from"] = out["partnerBillingPeriodFrom"]
    out["partner_billing_period_to"] = out["partnerBillingPeriodTo"]
    out["partner_confirmation_request_id"] = out["partnerConfirmationRequestId"]
    doc_url_raw = getattr(inv, "payment_confirmation_document_url", None) or ""
    doc_url = doc_url_raw.strip() or None
    doc_at = getattr(inv, "payment_confirmation_recorded_at", None)
    out["paymentConfirmationDocumentUrl"] = doc_url
    out["payment_confirmation_document_url"] = doc_url
    out["paymentConfirmationRecordedAt"] = doc_at.isoformat() if doc_at else None
    out["payment_confirmation_recorded_at"] = out["paymentConfirmationRecordedAt"]
    out["requiresPaymentConfirmationDocument"] = bool(
        eff == "paid"
        and inv.status not in ("canceled", "draft")
        and not doc_url
    )
    out["requires_payment_confirmation_document"] = out["requiresPaymentConfirmationDocument"]
    if include_lines:
        hints = time_entry_hints or {}
        lines = sorted(inv.line_items, key=lambda x: (x.sort_order, x.id))
        built: list[dict[str, Any]] = []
        for li in lines:
            row: dict[str, Any] = {
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
            sc = getattr(li, "source_currency", None)
            sa = getattr(li, "source_amount", None)
            fr = getattr(li, "fx_rate", None)
            if sc:
                row["sourceCurrency"] = sc
                row["source_currency"] = sc
            if sa is not None:
                row["sourceAmount"] = float(sa)
                row["source_amount"] = float(sa)
            if fr is not None:
                row["fxRate"] = float(fr)
                row["fx_rate"] = float(fr)
            row["sort_order"] = row["sortOrder"]
            row["line_kind"] = row["lineKind"]
            row["unit_amount"] = row["unitAmount"]
            row["line_total"] = row["lineTotal"]
            row["time_entry_id"] = row["timeEntryId"]
            row["expense_request_id"] = row["expenseRequestId"]
            if li.line_kind == "time" and li.time_entry_id:
                hint = hints.get(str(li.time_entry_id))
                if hint:
                    wd, auth_uid = hint
                    row["timeEntryWorkDate"] = wd.isoformat()
                    row["timeAuthorAuthUserId"] = auth_uid
                    row["time_entry_work_date"] = row["timeEntryWorkDate"]
                    row["time_author_auth_user_id"] = auth_uid
            built.append(row)
        out["lines"] = built
        out["line_items"] = built
    if include_payments:
        pays = sorted(inv.payments, key=lambda x: x.paid_at)
        built_pays: list[dict[str, Any]] = []
        for p in pays:
            paid_iso = p.paid_at.isoformat()
            created_iso = p.created_at.isoformat()
            built_pays.append(
                {
                    "id": p.id,
                    "amount": float(p.amount),
                    "paymentMethod": p.payment_method,
                    "payment_method": p.payment_method,
                    "note": p.note,
                    "recordedByAuthUserId": p.recorded_by_auth_user_id,
                    "recorded_by_auth_user_id": p.recorded_by_auth_user_id,
                    "paidAt": paid_iso,
                    "paid_at": paid_iso,
                    "createdAt": created_iso,
                    "created_at": created_iso,
                }
            )
        out["payments"] = built_pays
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
    rates = await _load_user_rates(session, None)
    cpr = ClientProjectRepository(session)
    proj = await cpr.get_by_id_global(project_id)
    pc = (getattr(proj, "currency", None) or "USD") if proj else "USD"
    all_project_entries = await _load_project_billable_entries(session, project_id)
    all_task_ids = {str(e.task_id) for e in all_project_entries if e.task_id}
    task_ids = {str(e.task_id) for e in entries if e.task_id} | all_task_ids
    tasks_map: dict[str, Any] = {}
    if task_ids:
        rows = (
            await session.execute(
                select(TimeManagerClientTaskModel).where(TimeManagerClientTaskModel.id.in_(list(task_ids)))
            )
        ).scalars().all()
        tasks_map = {str(r.id): r for r in rows}

    projects_map = {project_id: proj} if proj else {}
    package_splits: dict[str, Any] = {}
    if proj and is_hour_package_project(proj):
        _, package_splits = compute_entry_splits_for_project_entries(
            proj,
            all_project_entries,
            date_from=date_from,
            date_to=date_to,
            tasks_map=tasks_map,
        )
    entries, _dropped = deduplicate_entries_for_report(
        entries,
        projects_map=projects_map,
        rates_map=rates,
        tasks_map=tasks_map,
        package_splits=package_splits or None,
    )
    entries, _ = deduplicate_entries_for_report(
        entries,
        projects_map=projects_map,
        rates_map=rates,
        tasks_map=tasks_map,
        ignore_amount=True,
    )
    ids = [e.id for e in entries]
    invoiced = await repo.invoiced_time_entry_ids(ids)

    out: list[dict[str, Any]] = []
    for e in entries:
        if e.id in invoiced:
            continue
        h = dec(e.hours)
        qty = invoice_hours_for_billing(h)
        task = tasks_map.get(str(e.task_id)) if e.task_id else None
        split = package_splits.get(str(e.id)) if package_splits else None
        package_covered = False
        billable_hours = qty
        if proj and is_hour_package_project(proj) and not is_flat_fee_task(task):
            if not split or split.overage_hours <= 0:
                package_covered = True
                billable_hours = Decimal(0)
                amt = Decimal(0)
                cur = pc
            else:
                billable_hours = invoice_hours_for_billing(split.overage_hours)
                rate_amt, _rc = _billable_rate_for_entry(
                    e.work_date,
                    rates.get(e.auth_user_id),
                    project_currency=pc,
                    time_entry_project_id=e.project_id,
                    task=task,
                )
                unit = invoice_rate_for_billing(rate_amt)
                if unit > 0 and billable_hours > 0:
                    amt = money_product_hours_rate(billable_hours, unit)
                    cur = pc
                else:
                    amt, cur = _billable_amount_for_entry(
                        billable_hours,
                        True,
                        e.work_date,
                        rates.get(e.auth_user_id),
                        project_currency=pc,
                        time_entry_project_id=e.project_id,
                        task=task,
                    )
        else:
            rate_amt, _rc = _billable_rate_for_entry(
                e.work_date,
                rates.get(e.auth_user_id),
                project_currency=pc,
                time_entry_project_id=e.project_id,
                task=task,
            )
            unit = invoice_rate_for_billing(rate_amt)
            if unit > 0 and qty > 0:
                amt = money_product_hours_rate(qty, unit)
                cur = pc
            else:
                amt, cur = _billable_amount_for_entry(
                    qty,
                    e.is_billable,
                    e.work_date,
                    rates.get(e.auth_user_id),
                    project_currency=pc,
                    time_entry_project_id=e.project_id,
                    task=task,
                )
        out.append(
            {
                "id": e.id,
                "authUserId": e.auth_user_id,
                "workDate": e.work_date.isoformat(),
                "hours": float(qty),
                "billableHours": float(billable_hours),
                "roundedHours": float(qty),
                "durationSeconds": int(e.duration_seconds),
                "description": e.description,
                "billableAmount": float(_money4(amt)),
                "currency": cur,
                "sourceCurrency": (cur or pc),
                "packageCovered": package_covered,
                "coveredHours": float(split.covered_hours) if split else None,
                "overageHours": float(split.overage_hours) if split else None,
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
