"""Invoice-ready preview built strictly from a confirmed report snapshot.

Партнёрский счёт должен формироваться **строго из подтверждённого отчёта** (снимка), а не
пересобираться заново из «живых» записей БД. Иначе после подтверждения любые правки/добавления
записей времени приводят к расхождению сумм и числа строк со подписанным отчётом.

Здесь строки счёта берутся из строк снимка (`tt_report_snapshot_rows`, frozen_data + overrides)
по тем же правилам, что и подписанный партнёрский Excel-отчёт:

- в счёт попадает по одной строке на каждую включённую строку времени отчёта;
- ``hours = round2(hours)``, ``rate = round2(billableRate)``, ``amount = round2(hours * rate)``;
- описание строки — это ``note``/``description`` записи (без префикса задачи);
- суммы берутся в валюте отчёта и, если валюта счёта другая, конвертируются по FX.

Расходы (reimbursable) добавляются как и раньше — они не входят в снимок отчёта.

Если снимок «минимальный» (без построчной детализации, только маркер проекта), детализацию взять
неоткуда — тогда вызывающий код делает fallback на :func:`build_partner_confirmed_invoice_preview`.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.invoice_fx import FxConversion, FxRateBook, convert_or_same, load_fx_rate_book
from application.partner_confirmed_invoice_preview import (
    PartnerInvoicePreview,
    PartnerInvoicePreviewLine,
    build_partner_confirmed_invoice_preview,
)
from application.report_builder import _fetch_expense_report_data
from application.report_snapshot_overrides import merge_frozen_and_overrides
from infrastructure.models_reports import ReportSnapshotRowModel
from infrastructure.repositories import ClientProjectRepository
from infrastructure.repository_invoices import InvoiceRepository
from infrastructure.repository_partner_report_confirmations import (
    PartnerReportConfirmationRepository,
)

_Q2 = Decimal("0.01")
_Q4 = Decimal("0.0001")
_ZERO = Decimal(0)
_INVOICABLE_EXPENSE_STATUSES = frozenset({"approved", "paid", "closed"})


def _round2(v: Decimal) -> Decimal:
    return v.quantize(_Q2, rounding=ROUND_HALF_UP)


def _money4(v: Decimal) -> Decimal:
    return v.quantize(_Q4, rounding=ROUND_HALF_UP)


def _norm_ccy(v: str | None) -> str:
    return (v or "USD").strip().upper()[:10] or "USD"


def _pick_str(d: dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _pick_bool(d: dict[str, Any], *keys: str) -> bool:
    for k in keys:
        v = d.get(k)
        if v is True:
            return True
        if v is False or v is None:
            continue
        s = str(v).strip().lower()
        if s in ("true", "1", "yes"):
            return True
    return False


def _pick_num(d: dict[str, Any], *keys: str) -> Decimal | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            try:
                return Decimal(str(v))
            except Exception:
                continue
        if isinstance(v, str) and v.strip():
            try:
                return Decimal(v.strip().replace(" ", "").replace(",", "."))
            except Exception:
                continue
    return None


def _effective_row_data(row: ReportSnapshotRowModel) -> dict[str, Any]:
    try:
        frozen = json.loads(row.frozen_data_json or "{}")
    except (json.JSONDecodeError, TypeError):
        frozen = {}
    if not isinstance(frozen, dict):
        frozen = {}
    ovr: dict[str, Any] | None = None
    if row.overrides_json:
        try:
            parsed = json.loads(row.overrides_json)
            if isinstance(parsed, dict):
                ovr = parsed
        except (json.JSONDecodeError, TypeError):
            ovr = None
    return merge_frozen_and_overrides(frozen, ovr)


def _row_time_entry_id(row: ReportSnapshotRowModel, d: dict[str, Any]) -> str:
    te = _pick_str(d, "timeEntryId", "time_entry_id")
    if te:
        return te
    st = (row.source_type or "").strip().lower()
    if "time" in st or "entry" in st:
        return (row.source_id or "").strip()
    return ""


def _row_work_date(d: dict[str, Any]) -> str:
    wd = _pick_str(d, "workDate", "work_date")
    if not wd:
        rec = _pick_str(d, "recordedAt", "recorded_at")
        wd = rec[:10]
    return wd[:10]


def _is_included_billable_time_row(row: ReportSnapshotRowModel, d: dict[str, Any]) -> bool:
    """Те же правила включения, что и в подписанном партнёрском Excel-отчёте."""
    if _pick_bool(d, "isVoided", "is_voided"):
        return False
    rk = _pick_str(d, "rowKind", "row_kind").lower()
    if rk == "aggregate":
        return False
    st = (row.source_type or "").strip().lower()
    if "aggregate" in st or "rollup" in st or "summary" in st:
        return False
    if st in ("project", "projects", "client_project"):
        return False
    hours = _pick_num(
        d,
        "billableHours",
        "billable_hours",
        "hours",
        "durationHours",
        "duration_hours",
        "totalHours",
        "total_hours",
        "quantity",
    )
    if hours is None or hours <= Decimal("0.0000001"):
        return False
    if rk == "entry":
        return True
    te = _row_time_entry_id(row, d)
    wd = _row_work_date(d)
    if te and hours > 0:
        return True
    if wd and hours > 0:
        return True
    return False


def _within_period(wd: str, date_from: date, date_to: date) -> bool:
    s = (wd or "").strip()[:10]
    if not s:
        return True  # без даты не можем отфильтровать — включаем
    try:
        d = date.fromisoformat(s)
    except ValueError:
        return True
    return date_from <= d <= date_to


def _record_fx(fx_used: list[dict[str, Any]], conv: FxConversion) -> None:
    if conv.source_currency == conv.target_currency:
        return
    for row in fx_used:
        if (
            row.get("sourceCurrency") == conv.source_currency
            and row.get("targetCurrency") == conv.target_currency
            and abs(float(row.get("fxRate", 0)) - float(conv.fx_rate)) < 1e-12
        ):
            return
    fx_used.append(conv.as_dict())


async def build_invoice_preview_from_snapshot_rows(
    session: AsyncSession,
    *,
    snapshot_rows: list[ReportSnapshotRowModel],
    project_id: str,
    date_from: date,
    date_to: date,
    invoice_currency: str,
    issue_date: date | None = None,
    fx_book: FxRateBook | None = None,
    exclude_invoiced: bool = True,
) -> PartnerInvoicePreview | None:
    """Строит предпросмотр счёта строго из строк снимка отчёта.

    Возвращает ``None``, если в снимке нет построчной детализации по времени
    (минимальный снимок) — тогда источника истины нет и нужен fallback.
    """
    pid = (project_id or "").strip()
    inv_ccy = _norm_ccy(invoice_currency)
    on_date = issue_date or date_to
    book = fx_book or await load_fx_rate_book(session)
    fx_used: list[dict[str, Any]] = []

    cpr = ClientProjectRepository(session)
    proj = await cpr.get_by_id_global(pid)
    project_ccy = _norm_ccy(getattr(proj, "currency", None) or "USD")

    # 1. Собираем строки времени строго из снимка (frozen + overrides).
    raw_time: list[dict[str, Any]] = []
    saw_entry_rows = False
    for sr in sorted(snapshot_rows, key=lambda r: r.sort_order):
        d = _effective_row_data(sr)
        if not _is_included_billable_time_row(sr, d):
            continue
        saw_entry_rows = True
        wd = _row_work_date(d)
        if not _within_period(wd, date_from, date_to):
            continue
        hours = _pick_num(
            d,
            "billableHours",
            "billable_hours",
            "hours",
            "durationHours",
            "duration_hours",
            "totalHours",
            "total_hours",
            "quantity",
        ) or _ZERO
        rate = _pick_num(d, "billableRate", "billable_rate") or _ZERO
        hours2 = _round2(hours)
        rate2 = _round2(rate)
        if hours2 <= 0:
            continue
        amount2 = _round2(hours2 * rate2)
        desc = _pick_str(d, "note", "notes", "description") or f"Время {wd}".strip()
        src_ccy = _norm_ccy(_pick_str(d, "currency") or project_ccy)
        raw_time.append(
            {
                "time_entry_id": _row_time_entry_id(sr, d),
                "hours": hours2,
                "rate": rate2,
                "amount": amount2,
                "description": desc[:2000],
                "currency": src_ccy,
            }
        )

    if not saw_entry_rows:
        # Минимальный снимок без детализации — источника истины нет.
        return None

    repo = InvoiceRepository(session)

    # Дедуп по time_entry_id + исключение уже выставленных записей.
    linked_ids = [r["time_entry_id"] for r in raw_time if r["time_entry_id"]]
    invoiced: set[str] = set()
    if exclude_invoiced and linked_ids:
        invoiced = await repo.invoiced_time_entry_ids(linked_ids)

    lines: list[PartnerInvoicePreviewLine] = []
    time_ids: list[str] = []
    seen_ids: set[str] = set()
    time_sub = _ZERO
    for r in raw_time:
        te = r["time_entry_id"]
        if te:
            if te in seen_ids or te in invoiced:
                continue
            seen_ids.add(te)
        conv = convert_or_same(book, r["amount"], r["currency"], inv_ccy, on_date)
        _record_fx(fx_used, conv)
        unit_conv = (
            convert_or_same(book, r["rate"], r["currency"], inv_ccy, on_date).converted_amount
            if r["hours"] > 0
            else _ZERO
        )
        lines.append(
            PartnerInvoicePreviewLine(
                line_kind="time",
                description=r["description"],
                quantity=r["hours"],
                unit_amount=unit_conv,
                line_total=conv.converted_amount,
                source_currency=conv.source_currency,
                source_amount=conv.source_amount,
                fx_rate=conv.fx_rate,
                time_entry_id=te or None,
            )
        )
        if te:
            time_ids.append(te)
        time_sub += conv.converted_amount

    # 2. Расходы (reimbursable) — не входят в снимок отчёта, берём за период как раньше.
    expense_ids: list[str] = []
    expense_sub = _ZERO
    exp_rows = await _fetch_expense_report_data(date_from, date_to, None, [pid])
    candidates = [
        r
        for r in exp_rows
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

    expected = _money4(time_sub + expense_sub)
    return PartnerInvoicePreview(
        currency=inv_ccy,
        expected_subtotal=expected,
        time_subtotal=_money4(time_sub),
        expense_subtotal=_money4(expense_sub),
        package_fee_subtotal=_ZERO,
        time_entry_ids=time_ids,
        expense_ids=expense_ids,
        lines=lines,
        fx_used=fx_used,
        project_currency=project_ccy,
        dropped_duplicate_count=0,
    )


async def _load_confirmed_snapshot_rows(
    session: AsyncSession,
    *,
    project_id: str,
    date_from: date,
    date_to: date,
    partner_confirmation_request_id: str | None = None,
) -> list[ReportSnapshotRowModel]:
    conf_repo = PartnerReportConfirmationRepository(session)
    snapshot_id: str | None = None
    rid = (partner_confirmation_request_id or "").strip()
    if rid:
        req = await conf_repo.get_request_by_id(rid)
        if req is not None:
            snapshot_id = (req.snapshot_id or "").strip() or None
    if not snapshot_id:
        req = await conf_repo.find_confirmed_covering_project_period(
            project_id, date_from, date_to
        )
        if req is not None:
            snapshot_id = (req.snapshot_id or "").strip() or None
    if not snapshot_id:
        return []
    q = (
        select(ReportSnapshotRowModel)
        .where(ReportSnapshotRowModel.snapshot_id == snapshot_id)
        .order_by(ReportSnapshotRowModel.sort_order)
    )
    return list((await session.execute(q)).scalars().all())


async def resolve_partner_invoice_preview(
    session: AsyncSession,
    *,
    project_id: str,
    date_from: date,
    date_to: date,
    invoice_currency: str,
    issue_date: date | None = None,
    fx_book: FxRateBook | None = None,
    exclude_invoiced: bool = True,
    partner_confirmation_request_id: str | None = None,
) -> PartnerInvoicePreview:
    """Источник истины для партнёрского счёта.

    Сначала пытается построить строки строго из снимка подтверждённого отчёта; если снимок
    минимальный (без построчной детализации), делает fallback на сборку из живых записей.
    """
    snapshot_rows = await _load_confirmed_snapshot_rows(
        session,
        project_id=project_id,
        date_from=date_from,
        date_to=date_to,
        partner_confirmation_request_id=partner_confirmation_request_id,
    )
    if snapshot_rows:
        preview = await build_invoice_preview_from_snapshot_rows(
            session,
            snapshot_rows=snapshot_rows,
            project_id=project_id,
            date_from=date_from,
            date_to=date_to,
            invoice_currency=invoice_currency,
            issue_date=issue_date,
            fx_book=fx_book,
            exclude_invoiced=exclude_invoiced,
        )
        if preview is not None:
            return preview
    return await build_partner_confirmed_invoice_preview(
        session,
        project_id=project_id,
        date_from=date_from,
        date_to=date_to,
        invoice_currency=invoice_currency,
        issue_date=issue_date,
        fx_book=fx_book,
        exclude_invoiced=exclude_invoiced,
    )
