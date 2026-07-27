from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
        stmt = stmt.order_by(InvoiceRegistryRowModel.id.asc())
        return list((await self._s.execute(stmt)).scalars().all())

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

