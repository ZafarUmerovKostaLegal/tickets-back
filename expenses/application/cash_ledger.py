"""Apply reimbursed cash expenses to the company cash desk, matching the expenses bot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import (
    CashBalanceModel,
    CashMovementModel,
    CashTrackedModel,
    ExpenseRequestModel,
)


@dataclass(frozen=True)
class PaidCashExpense:
    id: str
    amount: Decimal
    description: str
    paid_at: datetime | None
    paid_by_user_id: int | None


@dataclass(frozen=True)
class CashLedgerAction:
    kind: Literal["track", "subtract", "restore"]
    expense_id: str
    amount: Decimal
    description: str = ""
    before: Decimal = Decimal("0")
    after: Decimal = Decimal("0")
    paid_at: datetime | None = None
    paid_by_user_id: int | None = None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_manual_cash_entry(kind: str, expense_id: str | None) -> bool:
    """Hand-typed expense or top-up. Rows linked to an expense request stay read-only."""
    return not (expense_id or "").strip() and kind in ("expense", "topup")


def manual_balance_delta(*, kind: str, old_amount: Decimal, new_amount: Decimal | None) -> Decimal:
    """How the live cash figure moves when a manual row changes. None deletes the row."""
    sign = Decimal("-1") if kind == "expense" else Decimal("1")
    old_effect = sign * old_amount
    new_effect = Decimal("0") if new_amount is None else sign * new_amount
    return new_effect - old_effect


def propagate_cash_delta(rows: list, delta: Decimal) -> bool:
    """Shift later history by delta until an absolute balance set.

    Returns True when the delta still applies to the live cash figure.
    A later «остаток установлен» keeps its own amount and stops the shift.
    """
    if delta == 0:
        return True
    for row in rows:
        if row.kind == "set":
            if row.balance_before is not None:
                row.balance_before = Decimal(row.balance_before) + delta
            return False
        if row.balance_before is not None:
            row.balance_before = Decimal(row.balance_before) + delta
        row.balance_after = Decimal(row.balance_after) + delta
    return True


def _reason(description: str, expense_id: str) -> str:
    text = " ".join((description or "").split())
    return text or expense_id


def plan_reimbursement_actions(
    *,
    balance: Decimal,
    baseline_done: bool,
    cutoff: datetime | None,
    tracked: dict[str, Decimal],
    paid: list[PaidCashExpense],
) -> tuple[bool, list[CashLedgerAction]]:
    """Opening balance already includes reimbursements paid at or before it was set.

    Later status «Возмещено» (paid + cash, not a partner expense) subtracts amount_uzs
    and is listed once. Rolling that status back adds the amount back and drops the row.
    """
    known = dict(tracked)
    actions: list[CashLedgerAction] = []
    done = baseline_done
    limit = None if cutoff is None else _as_utc(cutoff)

    if not done:
        for row in paid:
            paid_at = None if row.paid_at is None else _as_utc(row.paid_at)
            if paid_at is not None and limit is not None and paid_at > limit:
                continue
            if row.id in known:
                continue
            known[row.id] = row.amount
            actions.append(
                CashLedgerAction(
                    "track",
                    expense_id=row.id,
                    amount=row.amount,
                    description=_reason(row.description, row.id),
                )
            )
        done = True

    current = balance
    paid_ids = {row.id for row in paid}
    for row in paid:
        if row.id in known or row.amount <= 0:
            continue
        before = current
        current = current - row.amount
        known[row.id] = row.amount
        actions.append(
            CashLedgerAction(
                "subtract",
                expense_id=row.id,
                amount=row.amount,
                description=_reason(row.description, row.id),
                before=before,
                after=current,
                paid_at=row.paid_at,
                paid_by_user_id=row.paid_by_user_id,
            )
        )

    for expense_id, amount in list(tracked.items()):
        if expense_id in paid_ids:
            continue
        before = current
        current = current + amount
        actions.append(
            CashLedgerAction(
                "restore",
                expense_id=expense_id,
                amount=amount,
                before=before,
                after=current,
            )
        )
    return done, actions


async def _paid_cash_expenses(session: AsyncSession) -> list[PaidCashExpense]:
    rows = (
        await session.execute(
            select(ExpenseRequestModel)
            .where(
                ExpenseRequestModel.status == "paid",
                func.lower(func.coalesce(ExpenseRequestModel.payment_method, "")) == "cash",
                ExpenseRequestModel.expense_type != "partner_expense",
            )
            .order_by(ExpenseRequestModel.paid_at.asc().nulls_last(), ExpenseRequestModel.id.asc())
        )
    ).scalars().all()
    return [
        PaidCashExpense(
            id=row.id,
            amount=Decimal(row.amount_uzs),
            description=row.description or "",
            paid_at=row.paid_at,
            paid_by_user_id=row.paid_by_user_id,
        )
        for row in rows
    ]


async def sync_cash_reimbursements(session: AsyncSession, actor_user_id: int) -> None:
    """Fold new and cancelled cash reimbursements into the cash balance and history."""
    row = (
        await session.execute(
            select(CashBalanceModel).where(CashBalanceModel.id == 1).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or not row.balance_set or row.balance is None:
        return

    paid = await _paid_cash_expenses(session)
    tracked_rows = (await session.execute(select(CashTrackedModel))).scalars().all()
    tracked = {item.expense_id: Decimal(item.amount) for item in tracked_rows}
    done, actions = plan_reimbursement_actions(
        balance=Decimal(row.balance),
        baseline_done=bool(row.baseline_done),
        cutoff=row.balance_set_at or row.updated_at,
        tracked=tracked,
        paid=paid,
    )
    if not actions and bool(row.baseline_done) == done:
        return

    now = datetime.now(timezone.utc)
    balance = Decimal(row.balance)
    for action in actions:
        if action.kind == "track":
            session.add(
                CashTrackedModel(
                    expense_id=action.expense_id,
                    amount=action.amount,
                    description=action.description,
                )
            )
            continue
        if action.kind == "subtract":
            moment = action.paid_at or now
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            session.add(
                CashTrackedModel(
                    expense_id=action.expense_id,
                    amount=action.amount,
                    description=action.description,
                )
            )
            session.add(
                CashMovementModel(
                    kind="expense",
                    amount=action.amount,
                    note=action.description,
                    balance_before=action.before,
                    balance_after=action.after,
                    expense_id=action.expense_id,
                    created_by_user_id=action.paid_by_user_id or actor_user_id,
                    created_at=moment,
                )
            )
            balance = action.after
            continue
        await session.execute(
            delete(CashMovementModel).where(CashMovementModel.expense_id == action.expense_id)
        )
        await session.execute(
            delete(CashTrackedModel).where(CashTrackedModel.expense_id == action.expense_id)
        )
        balance = action.after

    row.balance = balance
    row.baseline_done = done
    row.updated_at = now
