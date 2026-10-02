from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, extract, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import TimeManagerClientModel
from infrastructure.models_invoices import InvoiceModel
from infrastructure.models_invoice_registry import (
    InvoiceRegistryArchiveSheetModel,
    InvoiceRegistryRowModel,
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _str(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def _registry_partner_codes(
    worker_ids: list[int],
    partner_by_worker: dict[int, int],
    initials_by_user: dict[int, str | None],
) -> str:
    """Partner initials for the people who logged the invoice hours. Several partners stay listed."""
    codes: list[str] = []
    seen: set[str] = set()
    for worker_id in worker_ids:
        partner_id = partner_by_worker.get(worker_id)
        if partner_id is None and worker_id in initials_by_user and any(
            partner == worker_id for partner in partner_by_worker.values()
        ):
            partner_id = worker_id
        raw = (initials_by_user.get(partner_id) or "").strip() if partner_id is not None else ""
        key = raw.upper()
        if not key or key in seen:
            continue
        seen.add(key)
        codes.append(raw)
    return ", ".join(codes)


def _seq_sort_key(seq: str) -> tuple[int, str]:
    digits = "".join(ch for ch in (seq or "") if ch.isdigit())
    return (int(digits) if digits else 10**9, seq or "")


def _money_cell(value: Any) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{n:,.2f}"


_SYSTEM_STATUS_LABELS = {
    "draft": "Черновик",
    "sent": "Отправлен",
    "viewed": "Просмотрен",
    "partial_paid": "Частично оплачен",
    "paid": "Оплачен",
    "canceled": "Отменён",
    "overdue": "Просрочен",
}


def _short_registry_details(internal_note: str, *, limit: int = 180) -> str:
    """Внутреннее примечание счёта, одной строкой. Текст работ из отчёта сюда не попадает."""
    text = " ".join((internal_note or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _system_status(inv: InvoiceModel) -> str:
    from datetime import date
    from decimal import Decimal

    raw = (inv.status or "").strip()
    if raw == "canceled":
        return _SYSTEM_STATUS_LABELS["canceled"]
    try:
        total = Decimal(str(inv.total_amount or 0))
        paid = Decimal(str(inv.amount_paid or 0))
    except Exception:
        total = Decimal(0)
        paid = Decimal(0)
    balance = total - paid
    if total > 0 and balance <= Decimal("0.009"):
        key = "paid"
    elif raw == "paid":
        key = "paid"
    elif paid > 0 and balance > 0:
        key = "partial_paid"
    else:
        key = raw or "draft"
    if key in ("sent", "viewed", "partial_paid") and inv.due_date and inv.due_date < date.today() and balance > 0:
        key = "overdue"
    return _SYSTEM_STATUS_LABELS.get(key, key)


class InvoiceRegistryRepository:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def list_2026_rows(self, q: str | None = None) -> list[InvoiceRegistryRowModel]:
        stmt = select(InvoiceRegistryRowModel).where(InvoiceRegistryRowModel.year == 2026)
        if q and q.strip():
            like = f"%{q.strip()}%"
            stmt = stmt.where(
                InvoiceRegistryRowModel.seq_no.ilike(like)
                | InvoiceRegistryRowModel.billed_to.ilike(like)
                | InvoiceRegistryRowModel.currency.ilike(like)
                | InvoiceRegistryRowModel.amount.ilike(like)
                | InvoiceRegistryRowModel.details.ilike(like)
                | InvoiceRegistryRowModel.partner.ilike(like)
                | InvoiceRegistryRowModel.issue_date.ilike(like)
                | InvoiceRegistryRowModel.due_or_payment.ilike(like)
                | InvoiceRegistryRowModel.client_number.ilike(like)
                | InvoiceRegistryRowModel.status_note.ilike(like)
                | InvoiceRegistryRowModel.advance_fee.ilike(like)
                | InvoiceRegistryRowModel.balance.ilike(like)
            )
        rows = list((await self._s.execute(stmt)).scalars().all())
        rows.sort(key=lambda row: (_seq_sort_key(row.seq_no), row.id))
        return rows

    async def count_system_invoices_2026(self) -> int:
        stmt = (
            select(func.count())
            .select_from(InvoiceModel)
            .where(extract("year", InvoiceModel.issue_date) == 2026)
        )
        return int((await self._s.execute(stmt)).scalar_one() or 0)

    async def list_system_invoice_rows_2026(self, q: str | None = None) -> list[dict[str, str]]:
        stmt = (
            select(InvoiceModel, TimeManagerClientModel.name)
            .join(TimeManagerClientModel, TimeManagerClientModel.id == InvoiceModel.client_id)
            .where(extract("year", InvoiceModel.issue_date) == 2026)
            .order_by(InvoiceModel.issue_date.asc(), InvoiceModel.invoice_number.asc())
        )
        if q and q.strip():
            like = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(
                    TimeManagerClientModel.name.ilike(like),
                    InvoiceModel.invoice_number.ilike(like),
                    InvoiceModel.currency.ilike(like),
                    InvoiceModel.client_note.ilike(like),
                    InvoiceModel.internal_note.ilike(like),
                    InvoiceModel.status.ilike(like),
                )
            )
        loaded = list((await self._s.execute(stmt)).all())
        workers_by_invoice: dict[str, list[int]] = {}
        ids = [inv.id for inv, _name in loaded]
        partner_by_worker: dict[int, int] = {}
        initials_by_user: dict[int, str | None] = {}
        if ids:
            from infrastructure.models import TimeEntryModel, TimeTrackingTeamMemberModel, TimeTrackingTeamModel
            from infrastructure.models_invoices import InvoiceLineItemModel

            line_stmt = (
                select(InvoiceLineItemModel.invoice_id, TimeEntryModel.auth_user_id)
                .outerjoin(TimeEntryModel, TimeEntryModel.id == InvoiceLineItemModel.time_entry_id)
                .where(InvoiceLineItemModel.invoice_id.in_(ids))
            )
            for invoice_id, auth_user_id in (await self._s.execute(line_stmt)).all():
                if auth_user_id is not None:
                    workers_by_invoice.setdefault(invoice_id, []).append(int(auth_user_id))
            team_rows = (
                await self._s.execute(
                    select(TimeTrackingTeamMemberModel.auth_user_id, TimeTrackingTeamModel.partner_auth_user_id)
                    .join(TimeTrackingTeamModel, TimeTrackingTeamModel.id == TimeTrackingTeamMemberModel.team_id)
                    .where(TimeTrackingTeamModel.is_archived.is_(False))
                )
            ).all()
            for worker_id, partner_id in team_rows:
                partner_by_worker.setdefault(int(worker_id), int(partner_id))
            worker_ids = {uid for group in workers_by_invoice.values() for uid in group}
            lookup_ids = set(partner_by_worker.values()) | worker_ids
            if lookup_ids:
                from application.user_initials import fetch_auth_initials_by_user_id

                initials_by_user = await fetch_auth_initials_by_user_id(sorted(lookup_ids))
        out: list[dict[str, str]] = []
        for index, (inv, client_name) in enumerate(loaded, start=1):
            total = inv.total_amount
            paid = inv.amount_paid
            try:
                balance = float(total or 0) - float(paid or 0)
            except (TypeError, ValueError):
                balance = 0
            details = _short_registry_details(inv.internal_note or "")
            out.append({
                "id": f"sys-{inv.id}",
                "invoiceId": str(inv.id),
                "seqNo": str(index),
                "billedTo": client_name or "",
                "currency": (inv.currency or "").strip().upper(),
                "amount": _money_cell(total),
                "details": details,
                "partner": _registry_partner_codes(
                    workers_by_invoice.get(inv.id, []),
                    partner_by_worker,
                    initials_by_user,
                ),
                "issueDate": inv.issue_date.isoformat() if inv.issue_date else "",
                "dueOrPayment": inv.due_date.isoformat() if inv.due_date else "",
                "clientNumber": inv.invoice_number or "",
                "statusNote": _system_status(inv),
                "advanceFee": "",
                "balance": _money_cell(balance) if balance > 0.009 else "",
            })
        return out

    async def count_2026_rows(self) -> int:
        stmt = select(func.count()).select_from(InvoiceRegistryRowModel).where(InvoiceRegistryRowModel.year == 2026)
        return int((await self._s.execute(stmt)).scalar_one() or 0)

    async def get_2026_row(self, row_id: str) -> InvoiceRegistryRowModel | None:
        stmt = select(InvoiceRegistryRowModel).where(
            InvoiceRegistryRowModel.year == 2026,
            InvoiceRegistryRowModel.id == row_id,
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def create_2026_row(self, payload: dict[str, Any], *, updated_by: int | None) -> InvoiceRegistryRowModel:
        row = InvoiceRegistryRowModel(
            id=_str(payload.get("id")).strip() or f"2026-new-{int(_now_utc().timestamp() * 1000)}",
            year=2026,
            seq_no=_str(payload.get("seqNo")),
            billed_to=_str(payload.get("billedTo")),
            currency=_str(payload.get("currency")),
            amount=_str(payload.get("amount")),
            details=_str(payload.get("details")),
            partner=_str(payload.get("partner")),
            issue_date=_str(payload.get("issueDate")),
            due_or_payment=_str(payload.get("dueOrPayment")),
            client_number=_str(payload.get("clientNumber")),
            status_note=_str(payload.get("statusNote")),
            advance_fee=_str(payload.get("advanceFee")),
            balance=_str(payload.get("balance")),
            updated_by=updated_by,
            created_at=_now_utc(),
            updated_at=_now_utc(),
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def patch_2026_row(self, row: InvoiceRegistryRowModel, patch: dict[str, Any], *, updated_by: int | None) -> InvoiceRegistryRowModel:
        if "seqNo" in patch:
            row.seq_no = _str(patch.get("seqNo"))
        if "billedTo" in patch:
            row.billed_to = _str(patch.get("billedTo"))
        if "currency" in patch:
            row.currency = _str(patch.get("currency"))
        if "amount" in patch:
            row.amount = _str(patch.get("amount"))
        if "details" in patch:
            row.details = _str(patch.get("details"))
        if "partner" in patch:
            row.partner = _str(patch.get("partner"))
        if "issueDate" in patch:
            row.issue_date = _str(patch.get("issueDate"))
        if "dueOrPayment" in patch:
            row.due_or_payment = _str(patch.get("dueOrPayment"))
        if "clientNumber" in patch:
            row.client_number = _str(patch.get("clientNumber"))
        if "statusNote" in patch:
            row.status_note = _str(patch.get("statusNote"))
        if "advanceFee" in patch:
            row.advance_fee = _str(patch.get("advanceFee"))
        if "balance" in patch:
            row.balance = _str(patch.get("balance"))
        row.updated_by = updated_by
        row.updated_at = _now_utc()
        await self._s.flush()
        return row

    async def replace_2026_rows(self, rows: list[dict[str, Any]], *, updated_by: int | None) -> int:
        await self._s.execute(delete(InvoiceRegistryRowModel).where(InvoiceRegistryRowModel.year == 2026))
        for idx, payload in enumerate(rows, start=1):
            rid = _str(payload.get("id")).strip() or f"2026-{idx}"
            self._s.add(
                InvoiceRegistryRowModel(
                    id=rid,
                    year=2026,
                    seq_no=_str(payload.get("seqNo")),
                    billed_to=_str(payload.get("billedTo")),
                    currency=_str(payload.get("currency")),
                    amount=_str(payload.get("amount")),
                    details=_str(payload.get("details")),
                    partner=_str(payload.get("partner")),
                    issue_date=_str(payload.get("issueDate")),
                    due_or_payment=_str(payload.get("dueOrPayment")),
                    client_number=_str(payload.get("clientNumber")),
                    status_note=_str(payload.get("statusNote")),
                    advance_fee=_str(payload.get("advanceFee")),
                    balance=_str(payload.get("balance")),
                    updated_by=updated_by,
                    created_at=_now_utc(),
                    updated_at=_now_utc(),
                )
            )
        await self._s.flush()
        return len(rows)

    async def delete_2026_row(self, row_id: str) -> bool:
        res = await self._s.execute(
            delete(InvoiceRegistryRowModel).where(
                InvoiceRegistryRowModel.year == 2026,
                InvoiceRegistryRowModel.id == row_id,
            )
        )
        return bool(res.rowcount and res.rowcount > 0)

    async def list_archive_sheets(self) -> list[InvoiceRegistryArchiveSheetModel]:
        stmt = select(InvoiceRegistryArchiveSheetModel).order_by(InvoiceRegistryArchiveSheetModel.year_id.asc())
        return list((await self._s.execute(stmt)).scalars().all())

    async def get_archive_sheet(self, year_id: str) -> InvoiceRegistryArchiveSheetModel | None:
        return await self._s.get(InvoiceRegistryArchiveSheetModel, year_id)

    async def upsert_archive_sheet(
        self,
        year_id: str,
        *,
        sheet_name: str,
        rows: list[dict[str, Any]],
    ) -> InvoiceRegistryArchiveSheetModel:
        import json

        payload = json.dumps(rows, ensure_ascii=False)
        existing = await self.get_archive_sheet(year_id)
        if existing is None:
            existing = InvoiceRegistryArchiveSheetModel(
                year_id=year_id,
                sheet_name=sheet_name,
                rows_json=payload,
                imported_at=_now_utc(),
            )
            self._s.add(existing)
        else:
            existing.sheet_name = sheet_name
            existing.rows_json = payload
            existing.imported_at = _now_utc()
        await self._s.flush()
        return existing

