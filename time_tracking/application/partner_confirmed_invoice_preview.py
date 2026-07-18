"""Invoice-ready totals for a partner-confirmed project period (time + package + expenses)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.duplicate_time_entries import deduplicate_entries_for_report
from application.entry_pricing import (
    _billable_rate_for_entry,
    billable_amount_respecting_package,
)
from application.invoice_fx import FxConversion, FxRateBook, convert_or_same, load_fx_rate_book
from application.package_billing import (
    compute_entry_splits_for_project_entries,
    is_hour_package_project,
    month_key,
    package_fee_description,
    package_fee_x,
    package_hours_n,
)
from application.report_builder import _fetch_expense_report_data, _load_user_rates
from application.report_builder import _d as dec
from application.task_billing import is_flat_fee_task
from infrastructure.models import (
    TimeEntryModel,
    TimeManagerClientTaskModel,
)
from infrastructure.repositories import ClientProjectRepository
from infrastructure.repository_invoices import InvoiceRepository

_Q2 = Decimal("0.01")
_Q6 = Decimal("0.000001")
_Q4 = Decimal("0.0001")
_ZERO = Decimal(0)
_INVOICABLE_EXPENSE_STATUSES = frozenset({"approved", "paid", "closed"})


def _money4(v: Decimal) -> Decimal:
    return v.quantize(_Q4, rounding=ROUND_HALF_UP)


def _norm_ccy(v: str | None) -> str:
    return (v or "USD").strip().upper()[:10] or "USD"


def _norm_text(v: str | None) -> str:
    return " ".join((v or "").strip().lower().split())


def _entry_duplicate_fingerprint(
    e: Any,
    task: Any | None,
    *,
    amount: Decimal,
    currency: str,
    project_id: str,
) -> str | None:
    """Отпечаток дубля — как в просмотре отчёта (`deduplicateTimeExcelPreviewRows`):
    сотрудник + дата + ИМЯ задачи + заметка + часы(6 зн.) + сумма(2 зн.) + валюта.

    Ключевое отличие от `deduplicate_entries_for_report`: здесь задача сравнивается
    по ИМЕНИ, а не по `task_id`. Это ловит дубли с разными карточками задачи, но
    одинаковым названием (отчёт их схлопывает, а раньше счёт — нет)."""
    wd = e.work_date.isoformat() if getattr(e, "work_date", None) else ""
    if len(wd) != 10:
        return None
    task_name = _norm_text(getattr(task, "name", None))
    note = _norm_text(getattr(e, "description", None))
    hours_key = str(dec(e.hours).quantize(_Q6, rounding=ROUND_HALF_UP))
    amount_key = str(amount.quantize(_Q2, rounding=ROUND_HALF_UP))
    return "\x1f".join(
        (
            (project_id or "").strip(),
            f"id:{e.auth_user_id}",
            wd,
            task_name,
            note,
            hours_key,
            amount_key,
            _norm_ccy(currency),
        )
    )


@dataclass
class PartnerInvoicePreviewLine:
    line_kind: str
    description: str
    quantity: Decimal
    unit_amount: Decimal
    line_total: Decimal
    source_currency: str
    source_amount: Decimal
    fx_rate: Decimal
    time_entry_id: str | None = None
    expense_request_id: str | None = None
    package_month: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "lineKind": self.line_kind,
            "description": self.description,
            "quantity": float(self.quantity),
            "unitAmount": float(self.unit_amount),
            "lineTotal": float(self.line_total),
            "sourceCurrency": self.source_currency,
            "sourceAmount": float(self.source_amount),
            "fxRate": float(self.fx_rate),
            "timeEntryId": self.time_entry_id,
            "expenseRequestId": self.expense_request_id,
            "packageMonth": self.package_month,
        }


@dataclass
class PartnerInvoicePreview:
    currency: str
    expected_subtotal: Decimal
    time_subtotal: Decimal
    expense_subtotal: Decimal
    package_fee_subtotal: Decimal
    time_entry_ids: list[str] = field(default_factory=list)
    expense_ids: list[str] = field(default_factory=list)
    lines: list[PartnerInvoicePreviewLine] = field(default_factory=list)
    fx_used: list[dict[str, Any]] = field(default_factory=list)
    project_currency: str = "USD"
    dropped_duplicate_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "expectedSubtotal": float(self.expected_subtotal),
            "timeSubtotal": float(self.time_subtotal),
            "expenseSubtotal": float(self.expense_subtotal),
            "packageFeeSubtotal": float(self.package_fee_subtotal),
            "timeEntryIds": list(self.time_entry_ids),
            "expenseIds": list(self.expense_ids),
            "lines": [ln.as_dict() for ln in self.lines],
            "fxUsed": list(self.fx_used),
            "projectCurrency": self.project_currency,
            "droppedDuplicateCount": self.dropped_duplicate_count,
        }


def _record_fx(fx_used: list[dict[str, Any]], conv: FxConversion) -> None:
    if conv.source_currency == conv.target_currency:
        return
    key = (conv.source_currency, conv.target_currency, float(conv.fx_rate))
    for row in fx_used:
        if (
            row.get("sourceCurrency") == key[0]
            and row.get("targetCurrency") == key[1]
            and abs(float(row.get("fxRate", 0)) - key[2]) < 1e-12
        ):
            return
    fx_used.append(conv.as_dict())


async def build_partner_confirmed_invoice_preview(
    session: AsyncSession,
    *,
    project_id: str,
    date_from: date,
    date_to: date,
    invoice_currency: str,
    issue_date: date | None = None,
    fx_book: FxRateBook | None = None,
    exclude_invoiced: bool = True,
) -> PartnerInvoicePreview:
    """Build invoice-ready lines for a confirmed period (same rules as create_invoice)."""
    pid = (project_id or "").strip()
    inv_ccy = _norm_ccy(invoice_currency)
    on_date = issue_date or date_to
    book = fx_book or await load_fx_rate_book(session)
    fx_used: list[dict[str, Any]] = []

    cpr = ClientProjectRepository(session)
    proj = await cpr.get_by_id_global(pid)
    if not proj:
        return PartnerInvoicePreview(
            currency=inv_ccy,
            expected_subtotal=_ZERO,
            time_subtotal=_ZERO,
            expense_subtotal=_ZERO,
            package_fee_subtotal=_ZERO,
        )
    project_ccy = _norm_ccy(getattr(proj, "currency", None) or "USD")

    repo = InvoiceRepository(session)
    q = (
        select(TimeEntryModel)
        .where(
            TimeEntryModel.project_id == pid,
            TimeEntryModel.work_date >= date_from,
            TimeEntryModel.work_date <= date_to,
            TimeEntryModel.is_billable.is_(True),
            TimeEntryModel.voided_at.is_(None),
        )
        .order_by(TimeEntryModel.work_date, TimeEntryModel.id)
    )
    entries = list((await session.execute(q)).scalars().all())
    rates = await _load_user_rates(session, None)
    task_ids = {str(e.task_id) for e in entries if e.task_id}
    # Package splits need all project billable entries for carry-in correctness
    all_project_entries = list(
        (
            await session.execute(
                select(TimeEntryModel).where(
                    TimeEntryModel.project_id == pid,
                    TimeEntryModel.voided_at.is_(None),
                    TimeEntryModel.is_billable.is_(True),
                )
            )
        ).scalars().all()
    )
    all_task_ids = {str(e.task_id) for e in all_project_entries if e.task_id} | task_ids
    tasks_map: dict[str, Any] = {}
    if all_task_ids:
        rows = (
            await session.execute(
                select(TimeManagerClientTaskModel).where(
                    TimeManagerClientTaskModel.id.in_(list(all_task_ids))
                )
            )
        ).scalars().all()
        tasks_map = {str(r.id): r for r in rows}

    projects_map: dict[str, Any] = {pid: proj}
    package_splits: dict[str, Any] = {}
    package_months: set[tuple[int, int]] = set()
    if is_hour_package_project(proj):
        _, package_splits = compute_entry_splits_for_project_entries(
            proj,
            all_project_entries,
            date_from=date_from,
            date_to=date_to,
            tasks_map=tasks_map,
        )
        y, m = date_from.year, date_from.month
        ey, em = date_to.year, date_to.month
        while (y, m) <= (ey, em):
            package_months.add((y, m))
            if m == 12:
                y, m = y + 1, 1
            else:
                m += 1

    # Match report preview: minute-rounded hours + package-aware amount, then identity collapse.
    entries, dropped = deduplicate_entries_for_report(
        entries,
        projects_map=projects_map,
        rates_map=rates,
        tasks_map=tasks_map,
        package_splits=package_splits or None,
    )
    entries, dropped_id = deduplicate_entries_for_report(
        entries,
        projects_map=projects_map,
        rates_map=rates,
        tasks_map=tasks_map,
        ignore_amount=True,
    )
    dropped += dropped_id

    invoiced: set[str] = set()
    if exclude_invoiced and entries:
        invoiced = await repo.invoiced_time_entry_ids([e.id for e in entries])

    lines: list[PartnerInvoicePreviewLine] = []
    time_ids: list[str] = []
    time_sub = _ZERO
    seen_fp: set[str] = set()

    for e in entries:
        if e.id in invoiced:
            continue
        task = tasks_map.get(str(e.task_id)) if e.task_id else None
        user_rates = rates.get(e.auth_user_id)
        split = package_splits.get(str(e.id)) if package_splits else None
        if is_hour_package_project(proj) and e.work_date:
            package_months.add(month_key(e.work_date))

        # ЕДИНЫЙ ИСТОЧНИК ИСТИНЫ — подтверждённый отчёт.
        # Сумма строки считается ровно как `amount_to_pay` в отчёте: точные часы
        # (e.hours) с учётом пакета, БЕЗ предварительного округления часов до 2 знаков.
        # Раньше счёт округлял часы (invoice_hours_for_billing) → 2.63 ч вместо 2:38,
        # из-за чего суммы расходились с отчётом (394.50 против 395.00).
        src_amt, _cur = billable_amount_respecting_package(
            dec(e.hours),
            bool(e.is_billable),
            e.work_date,
            user_rates,
            project_currency=project_ccy,
            time_entry_project_id=e.project_id,
            task=task,
            package_split=split,
        )
        src_total = _money4(src_amt)
        if src_total <= 0:
            # Полностью покрыто пакетом или неоплачиваемо → в отчёте вклад 0,
            # отдельной строки в счёте не создаём.
            continue

        # Схлопываем дубли так же, как просмотр отчёта: по отпечатку строки
        # (сотрудник+дата+имя задачи+заметка+часы+сумма). Это убирает лишние строки,
        # которых нет в подтверждённом отчёте (в т.ч. дубли с разными карточками задачи).
        fp = _entry_duplicate_fingerprint(
            e, task, amount=src_total, currency=project_ccy, project_id=pid
        )
        if fp:
            if fp in seen_fp:
                dropped += 1
                continue
            seen_fp.add(fp)

        rate_amt, _rate_cur = _billable_rate_for_entry(
            e.work_date,
            user_rates,
            project_currency=project_ccy,
            time_entry_project_id=e.project_id,
            task=task,
        )
        desc = (e.description or "").strip() or f"Время {e.work_date.isoformat()}"

        if is_flat_fee_task(task):
            qty = Decimal(1)
            unit_src = src_total
        else:
            # Количество = точные часы, ставка = договорная ставка отчёта.
            # qty * unit == src_total на полной точности (как в отчёте: 2:38 × 150 = 395.00).
            if is_hour_package_project(proj) and split is not None:
                qty = dec(getattr(split, "overage_hours", 0))
            else:
                qty = dec(e.hours)
            if qty <= 0:
                continue
            if rate_amt and rate_amt > 0:
                unit_src = _money4(rate_amt)
            else:
                unit_src = _money4(src_total / qty)

        conv = convert_or_same(book, src_total, project_ccy, inv_ccy, on_date)
        _record_fx(fx_used, conv)
        unit_conv = (
            convert_or_same(book, unit_src, project_ccy, inv_ccy, on_date).converted_amount
            if qty > 0
            else _ZERO
        )
        lines.append(
            PartnerInvoicePreviewLine(
                line_kind="time",
                description=desc[:2000],
                quantity=qty,
                unit_amount=unit_conv,
                line_total=conv.converted_amount,
                source_currency=conv.source_currency,
                source_amount=conv.source_amount,
                fx_rate=conv.fx_rate,
                time_entry_id=e.id,
            )
        )
        time_ids.append(e.id)
        time_sub += conv.converted_amount

    package_sub = _ZERO
    if is_hour_package_project(proj):
        fee = package_fee_x(proj)
        n = package_hours_n(proj)
        if fee > 0:
            for y, m in sorted(package_months):
                marker = f"[package_fee:{pid}:{y:04d}-{m:02d}]"
                desc = f"{marker} {package_fee_description(proj.name or pid, y, m, n)}"
                conv = convert_or_same(book, _money4(fee), project_ccy, inv_ccy, on_date)
                _record_fx(fx_used, conv)
                lines.append(
                    PartnerInvoicePreviewLine(
                        line_kind="package_fee",
                        description=desc[:2000],
                        quantity=Decimal(1),
                        unit_amount=conv.converted_amount,
                        line_total=conv.converted_amount,
                        source_currency=conv.source_currency,
                        source_amount=conv.source_amount,
                        fx_rate=conv.fx_rate,
                        package_month=f"{y:04d}-{m:02d}",
                    )
                )
                package_sub += conv.converted_amount

    expense_ids: list[str] = []
    expense_sub = _ZERO
    rows = await _fetch_expense_report_data(date_from, date_to, None, [pid])
    candidates = [
        r
        for r in rows
        if r.get("is_reimbursable")
        and r.get("id")
        and (r.get("status") or "").strip() in _INVOICABLE_EXPENSE_STATUSES
    ]
    eids = [str(r["id"]) for r in candidates]
    exp_invoiced: set[str] = set()
    if exclude_invoiced and eids:
        exp_invoiced = await repo.invoiced_expense_ids(eids)
    for r in candidates:
        eid = str(r["id"])
        if eid in exp_invoiced:
            continue
        # Expenses are stored as USD equivalent
        src_amt = _money4(Decimal(str(r.get("equivalent_amount", 0) or 0)))
        conv = convert_or_same(book, src_amt, "USD", inv_ccy, on_date)
        _record_fx(fx_used, conv)
        desc = str(r.get("description") or "Расход")[:2000]
        lines.append(
            PartnerInvoicePreviewLine(
                line_kind="expense",
                description=desc,
                quantity=Decimal(1),
                unit_amount=conv.converted_amount,
                line_total=conv.converted_amount,
                source_currency=conv.source_currency,
                source_amount=conv.source_amount,
                fx_rate=conv.fx_rate,
                expense_request_id=eid,
            )
        )
        expense_ids.append(eid)
        expense_sub += conv.converted_amount

    expected = _money4(time_sub + expense_sub + package_sub)
    return PartnerInvoicePreview(
        currency=inv_ccy,
        expected_subtotal=expected,
        time_subtotal=_money4(time_sub),
        expense_subtotal=_money4(expense_sub),
        package_fee_subtotal=_money4(package_sub),
        time_entry_ids=time_ids,
        expense_ids=expense_ids,
        lines=lines,
        fx_used=fx_used,
        project_currency=project_ccy,
        dropped_duplicate_count=dropped,
    )
