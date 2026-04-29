

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from infrastructure.models_invoices import (
    InvoiceAuditLogModel,
    InvoiceCounterModel,
    InvoiceLineItemModel,
    InvoiceModel,
    InvoicePaymentModel,
)
from infrastructure.repository_shared import _now_utc

_Q4 = Decimal("0.0001")


def _m4(v: Decimal) -> Decimal:
    return v.quantize(_Q4, rounding=ROUND_HALF_UP)


def _sync_orm_payment_status(inv: InvoiceModel) -> None:

    if inv.status in ("canceled", "draft"):
        return
    bal = _m4(inv.total_amount - inv.amount_paid)
    if inv.total_amount > 0 and bal <= 0:
        inv.status = "paid"
    elif inv.amount_paid > 0:
        inv.status = "partial_paid"


class InvoiceRepository:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def allocate_next_seq(self, year: int) -> int:
        row = await self._s.get(InvoiceCounterModel, year)
        now = _now_utc()
        if row is None:
            row = InvoiceCounterModel(year=year, last_seq=1)
            self._s.add(row)
            await self._s.flush()
            return 1
        row.last_seq = int(row.last_seq or 0) + 1
        await self._s.flush()
        return row.last_seq

    async def get(self, invoice_id: str) -> InvoiceModel | None:
        q = select(InvoiceModel).where(InvoiceModel.id == invoice_id)
        return (await self._s.execute(q)).scalar_one_or_none()

    async def get_with_children(self, invoice_id: str) -> InvoiceModel | None:
        q = (
            select(InvoiceModel)
            .where(InvoiceModel.id == invoice_id)
            .options(
                selectinload(InvoiceModel.line_items),
                selectinload(InvoiceModel.payments),
                selectinload(InvoiceModel.audit_logs),
            )
        )
        return (await self._s.execute(q)).scalar_one_or_none()

    def _sum_payments_scalar_subq(self):
        return (
            select(func.coalesce(func.sum(InvoicePaymentModel.amount), 0))
            .where(InvoicePaymentModel.invoice_id == InvoiceModel.id)
            .scalar_subquery()
        )

    def _where_invoice_filters(
        self,
        q,
        *,
        client_id: str | None = None,
        project_id: str | None = None,
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ):
        if client_id:
            q = q.where(InvoiceModel.client_id == client_id)
        if project_id:
            q = q.where(InvoiceModel.project_id == project_id)
        if status:
            sum_paid_sq = self._sum_payments_scalar_subq()
            if status == "overdue":
                today = date.today()
                q = q.where(
                    and_(
                        InvoiceModel.status.in_(("sent", "viewed", "partial_paid")),
                        InvoiceModel.due_date < today,
                        sum_paid_sq < InvoiceModel.total_amount,
                    )
                )
            elif status == "paid":
                q = q.where(
                    or_(
                        InvoiceModel.status == "paid",
                        and_(
                            InvoiceModel.status.notin_(("canceled", "draft")),
                            InvoiceModel.total_amount > 0,
                            sum_paid_sq >= InvoiceModel.total_amount,
                        ),
                    )
                )
            elif status == "partial_paid":
                q = q.where(
                    or_(
                        InvoiceModel.status == "partial_paid",
                        and_(
                            InvoiceModel.status.notin_(("canceled", "draft")),
                            InvoiceModel.total_amount > 0,
                            sum_paid_sq > 0,
                            sum_paid_sq < InvoiceModel.total_amount,
                        ),
                    )
                )
            else:
                q = q.where(InvoiceModel.status == status)
        if date_from:
            q = q.where(InvoiceModel.issue_date >= date_from)
        if date_to:
            q = q.where(InvoiceModel.issue_date <= date_to)
        return q

    async def count_invoices(
        self,
        *,
        client_id: str | None = None,
        project_id: str | None = None,
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> int:
        q = self._where_invoice_filters(
            select(func.count()).select_from(InvoiceModel),
            client_id=client_id,
            project_id=project_id,
            status=status,
            date_from=date_from,
            date_to=date_to,
        )
        return int((await self._s.execute(q)).scalar_one() or 0)

    async def list_invoices(
        self,
        *,
        client_id: str | None = None,
        project_id: str | None = None,
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[InvoiceModel]:
        q = self._where_invoice_filters(
            select(InvoiceModel),
            client_id=client_id,
            project_id=project_id,
            status=status,
            date_from=date_from,
            date_to=date_to,
        )
        q = q.order_by(InvoiceModel.issue_date.desc(), InvoiceModel.invoice_number.desc())
        q = q.limit(min(limit, 500)).offset(offset)
        rows = list((await self._s.execute(q)).scalars().all())
        await self._apply_batch_payment_totals(rows)
        return rows

    async def list_invoices_for_aggregation(
        self,
        *,
        client_id: str | None = None,
        project_id: str | None = None,
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        cap: int = 50_000,
    ) -> list[InvoiceModel]:

        q = self._where_invoice_filters(
            select(InvoiceModel),
            client_id=client_id,
            project_id=project_id,
            status=status,
            date_from=date_from,
            date_to=date_to,
        )
        q = q.order_by(InvoiceModel.issue_date.desc(), InvoiceModel.invoice_number.desc()).limit(cap)
        rows = list((await self._s.execute(q)).scalars().all())
        await self._apply_batch_payment_totals(rows)
        return rows

    async def sum_payments_batch(self, invoice_ids: list[str]) -> dict[str, Decimal]:
        if not invoice_ids:
            return {}
        q = (
            select(
                InvoicePaymentModel.invoice_id,
                func.coalesce(func.sum(InvoicePaymentModel.amount), 0),
            )
            .where(InvoicePaymentModel.invoice_id.in_(invoice_ids))
            .group_by(InvoicePaymentModel.invoice_id)
        )
        raw = (await self._s.execute(q)).all()
        return {str(r[0]): _m4(Decimal(str(r[1]))) for r in raw}

    async def _apply_batch_payment_totals(self, rows: list[InvoiceModel]) -> None:
        if not rows:
            return
        sums = await self.sum_payments_batch([r.id for r in rows])
        for inv in rows:
            inv.amount_paid = sums.get(inv.id, Decimal(0))
            _sync_orm_payment_status(inv)

    async def reconcile_paid_fields(self, inv: InvoiceModel) -> None:
        inv.amount_paid = _m4(await self.sum_payments(inv.id))
        _sync_orm_payment_status(inv)

    async def time_entry_on_active_invoice(
        self, time_entry_id: str, exclude_invoice_id: str | None = None
    ) -> str | None:

        cond = [
            InvoiceLineItemModel.time_entry_id == time_entry_id,
            InvoiceModel.status != "canceled",
            InvoiceLineItemModel.time_entry_id.is_not(None),
        ]
        if exclude_invoice_id:
            cond.append(InvoiceModel.id != exclude_invoice_id)
        q = (
            select(InvoiceModel.id)
            .select_from(InvoiceLineItemModel)
            .join(InvoiceModel, InvoiceModel.id == InvoiceLineItemModel.invoice_id)
            .where(and_(*cond))
            .limit(1)
        )
        r = (await self._s.execute(q)).scalar_one_or_none()
        return str(r) if r else None

    async def expense_on_active_invoice(
        self, expense_request_id: str, exclude_invoice_id: str | None = None
    ) -> str | None:
        cond = [
            InvoiceLineItemModel.expense_request_id == expense_request_id,
            InvoiceModel.status != "canceled",
            InvoiceLineItemModel.expense_request_id.is_not(None),
        ]
        if exclude_invoice_id:
            cond.append(InvoiceModel.id != exclude_invoice_id)
        q = (
            select(InvoiceModel.id)
            .select_from(InvoiceLineItemModel)
            .join(InvoiceModel, InvoiceModel.id == InvoiceLineItemModel.invoice_id)
            .where(and_(*cond))
            .limit(1)
        )
        r = (await self._s.execute(q)).scalar_one_or_none()
        return str(r) if r else None

    async def invoiced_time_entry_ids(self, time_entry_ids: list[str]) -> set[str]:
        if not time_entry_ids:
            return set()
        q = (
            select(InvoiceLineItemModel.time_entry_id)
            .join(InvoiceModel, InvoiceModel.id == InvoiceLineItemModel.invoice_id)
            .where(
                and_(
                    InvoiceLineItemModel.time_entry_id.in_(time_entry_ids),
                    InvoiceModel.status != "canceled",
                )
            )
        )
        rows = (await self._s.execute(q)).all()
        return {str(r[0]) for r in rows if r[0]}

    async def invoiced_expense_ids(self, expense_ids: list[str]) -> set[str]:
        if not expense_ids:
            return set()
        q = (
            select(InvoiceLineItemModel.expense_request_id)
            .join(InvoiceModel, InvoiceModel.id == InvoiceLineItemModel.invoice_id)
            .where(
                and_(
                    InvoiceLineItemModel.expense_request_id.in_(expense_ids),
                    InvoiceModel.status != "canceled",
                )
            )
        )
        rows = (await self._s.execute(q)).all()
        return {str(r[0]) for r in rows if r[0]}

    def add(self, inv: InvoiceModel) -> None:
        self._s.add(inv)

    def add_line(self, line: InvoiceLineItemModel) -> None:
        self._s.add(line)

    async def delete_lines(self, invoice_id: str) -> None:
        await self._s.execute(
            InvoiceLineItemModel.__table__.delete().where(InvoiceLineItemModel.invoice_id == invoice_id)
        )

    async def add_payment(self, p: InvoicePaymentModel) -> None:
        self._s.add(p)

    async def add_audit(self, log: InvoiceAuditLogModel) -> None:
        self._s.add(log)

    async def sum_payments(self, invoice_id: str) -> Decimal:
        q = select(func.coalesce(func.sum(InvoicePaymentModel.amount), 0)).where(
            InvoicePaymentModel.invoice_id == invoice_id
        )
        v = (await self._s.execute(q)).scalar_one()
        return Decimal(str(v)) if v is not None else Decimal(0)
