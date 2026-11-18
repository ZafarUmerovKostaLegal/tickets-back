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
    InvoiceRegistryUsdModel,
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _str(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


# Placeholder initials of test / demo accounts: never shown as an invoice partner.
_PLACEHOLDER_INITIALS = frozenset({"TEST", "TST", "DEMO", "?"})


def _is_placeholder_initials(code: str) -> bool:
    key = (code or "").strip().upper()
    return not key or key in _PLACEHOLDER_INITIALS or key.startswith("TEST")


def _initials_for_users(
    user_ids: list[int],
    initials_by_user: dict[int, str | None],
    names_by_user: dict[int, tuple[str | None, str | None]] | None = None,
) -> str:
    """Initials of the given users in order, each once. Without stored initials they are built
    from the display name (as everywhere else in the hub); test accounts are skipped."""
    from application.user_initials import initials_from_display_name

    codes: list[str] = []
    seen: set[str] = set()
    for user_id in user_ids:
        raw = (initials_by_user.get(user_id) or "").strip()
        if not raw and names_by_user and user_id in names_by_user:
            name, email = names_by_user[user_id]
            raw = initials_from_display_name(name, email)
        key = raw.upper()
        if _is_placeholder_initials(key) or key in seen:
            continue
        seen.add(key)
        codes.append(raw)
    return ", ".join(codes)


def invoice_partner_project_ids(invoice_project_id: str | None, related_project_ids: list[str]) -> list[str]:
    """The invoice's own project; projects of its lines / partner report only when it has none."""
    own = (invoice_project_id or "").strip()
    if own:
        return [own]
    out: list[str] = []
    for pid in related_project_ids:
        pid = (pid or "").strip()
        if pid and pid not in out:
            out.append(pid)
    return out


def registry_sheet_partner(
    project_ids: list[str],
    *,
    partners_by_project: dict[str, list[int]],
    initials_by_user: dict[int, str | None],
    names_by_user: dict[int, tuple[str | None, str | None]] | None = None,
) -> str:
    """Partners of the invoiced project, strictly from the project: one partner gives one code,
    several give all of them. Team leads, the invoice author and test accounts are not used."""
    partner_ids: list[int] = []
    for project_id in project_ids:
        for user_id in partners_by_project.get(project_id, []):
            uid = int(user_id)
            if uid not in partner_ids:
                partner_ids.append(uid)
    return _initials_for_users(partner_ids, initials_by_user, names_by_user)


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
    "issued": "Выставлен",
    "viewed": "Просмотрен",
    "partial_paid": "Частично оплачен",
    "paid": "Оплачен",
    "client_paid": "Оплачен клиентом",
    "send_to_olga": "Ольге направить",
    "client_review": "На согласовании с Клиентом",
    "awaiting_client": "Жду подтверждения от клиента",
    "canceled": "Отменён",
    "overdue": "Просрочен",
}


def _registry_partner_note(client_note: str, *, limit: int = 600) -> str:
    """Примечание для партнёра: текст client_note, с переносами строк."""
    text = (client_note or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _short_registry_details(internal_note: str, *, limit: int = 4000) -> str:
    """Внутреннее примечание счёта целиком (с переносами строк): ячейка реестра редактирует именно его,
    поэтому обрезать или склеивать строки нельзя — иначе сохранение перезапишет полный текст огрызком.
    Лимит — только защита от аномально длинного текста."""
    text = (internal_note or "").replace("\r\n", "\n").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _system_status(inv: InvoiceModel) -> str:
    from datetime import date
    from decimal import Decimal

    raw = (inv.status or "").strip()
    if raw in ("issued", "client_paid", "send_to_olga", "client_review", "awaiting_client", "canceled"):
        return _SYSTEM_STATUS_LABELS[raw]
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

    async def list_system_invoice_rows_2026(
        self,
        q: str | None = None,
        *,
        authorization: str | None = None,
    ) -> list[dict[str, str]]:
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
        projects_by_invoice: dict[str, list[str]] = {}
        ids = [inv.id for inv, _name in loaded]
        partners_by_project: dict[str, list[int]] = {}
        initials_by_user: dict[int, str | None] = {}
        names_by_user: dict[int, tuple[str | None, str | None]] = {}

        def remember_project(invoice_id: str, project_id: str | None) -> None:
            pid = (project_id or "").strip()
            if not pid:
                return
            bucket = projects_by_invoice.setdefault(invoice_id, [])
            if pid not in bucket:
                bucket.append(pid)

        if ids:
            from application.project_partner_users import list_partner_auth_user_ids_by_projects
            from infrastructure.models import TimeEntryModel, TimeTrackingUserModel
            from infrastructure.models_invoices import InvoiceLineItemModel
            from infrastructure.models_reports import ReportPartnerConfirmationRequestModel
            from infrastructure.repository_access import UserProjectAccessRepository

            # Projects of lines / partner report matter only for invoices without their own project.
            orphans = {inv.id: inv for inv, _name in loaded if not (inv.project_id or "").strip()}
            if orphans:
                line_stmt = (
                    select(InvoiceLineItemModel.invoice_id, TimeEntryModel.project_id)
                    .join(TimeEntryModel, TimeEntryModel.id == InvoiceLineItemModel.time_entry_id)
                    .where(InvoiceLineItemModel.invoice_id.in_(list(orphans)))
                )
                for invoice_id, project_id in (await self._s.execute(line_stmt)).all():
                    remember_project(invoice_id, project_id)
                conf_ids = [
                    str(inv.partner_confirmation_request_id).strip()
                    for inv in orphans.values()
                    if str(inv.partner_confirmation_request_id or "").strip()
                ]
                if conf_ids:
                    conf_rows = (
                        await self._s.execute(
                            select(
                                ReportPartnerConfirmationRequestModel.id,
                                ReportPartnerConfirmationRequestModel.project_id,
                            ).where(ReportPartnerConfirmationRequestModel.id.in_(conf_ids))
                        )
                    ).all()
                    conf_project = {str(request_id): project_id for request_id, project_id in conf_rows}
                    for inv in orphans.values():
                        request_id = str(inv.partner_confirmation_request_id or "").strip()
                        if request_id:
                            remember_project(inv.id, conf_project.get(request_id))
            for inv, _name in loaded:
                projects_by_invoice[inv.id] = invoice_partner_project_ids(
                    inv.project_id, projects_by_invoice.get(inv.id, [])
                )
            project_ids = sorted({pid for group in projects_by_invoice.values() for pid in group})
            if project_ids:
                partners_by_project = await list_partner_auth_user_ids_by_projects(
                    self._s,
                    UserProjectAccessRepository(self._s),
                    project_ids,
                    authorization=authorization,
                )
            partner_ids = sorted({int(uid) for group in partners_by_project.values() for uid in group})
            if partner_ids:
                from application.user_initials import fetch_auth_initials_by_user_id

                initials_by_user = await fetch_auth_initials_by_user_id(partner_ids)
                name_rows = await self._s.execute(
                    select(
                        TimeTrackingUserModel.auth_user_id,
                        TimeTrackingUserModel.display_name,
                        TimeTrackingUserModel.email,
                    ).where(TimeTrackingUserModel.auth_user_id.in_(partner_ids))
                )
                names_by_user = {int(uid): (name, email) for uid, name, email in name_rows.all()}
        out: list[dict[str, str]] = []
        for index, (inv, client_name) in enumerate(loaded, start=1):
            total = inv.total_amount
            paid = inv.amount_paid
            try:
                balance = float(total or 0) - float(paid or 0)
            except (TypeError, ValueError):
                balance = 0
            details = _short_registry_details(inv.internal_note or "")
            partner_note = _registry_partner_note(inv.client_note or "")
            partner = registry_sheet_partner(
                projects_by_invoice.get(inv.id, []),
                partners_by_project=partners_by_project,
                initials_by_user=initials_by_user,
                names_by_user=names_by_user,
            )
            out.append({
                "id": f"sys-{inv.id}",
                "invoiceId": str(inv.id),
                "seqNo": str(index),
                "billedTo": client_name or "",
                "currency": (inv.currency or "").strip().upper(),
                "amount": _money_cell(total),
                "partnerNote": partner_note,
                "details": details,
                "partner": partner,
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

    async def usd_snapshots(self, year_id: str, row_ids: list[str]) -> dict[str, InvoiceRegistryUsdModel]:
        ids = [row_id for row_id in row_ids if row_id][:5000]
        if not ids:
            return {}
        stmt = select(InvoiceRegistryUsdModel).where(
            InvoiceRegistryUsdModel.year_id == year_id,
            InvoiceRegistryUsdModel.row_id.in_(ids),
        )
        rows = list((await self._s.execute(stmt)).scalars().all())
        return {row.row_id: row for row in rows}

    async def save_usd_snapshots(self, year_id: str, items: list[dict[str, str]]) -> int:
        saved = 0
        for item in items:
            row_id = item["row_id"]
            existing = (
                await self._s.execute(
                    select(InvoiceRegistryUsdModel).where(
                        InvoiceRegistryUsdModel.year_id == year_id,
                        InvoiceRegistryUsdModel.row_id == row_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                self._s.add(
                    InvoiceRegistryUsdModel(
                        year_id=year_id,
                        row_id=row_id,
                        amount_usd=item["amount_usd"],
                        basis=item["basis"],
                        updated_at=_now_utc(),
                    )
                )
            else:
                existing.amount_usd = item["amount_usd"]
                existing.basis = item["basis"]
                existing.updated_at = _now_utc()
            saved += 1
        await self._s.flush()
        return saved

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

