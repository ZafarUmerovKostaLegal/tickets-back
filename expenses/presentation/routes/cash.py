"""Company cash desk. Visible and writable only for partners."""

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.cash_ledger import sync_cash_reimbursements
from application.cash_money import cash_movement, format_money, parse_amount
from infrastructure.database import get_session
from infrastructure.models import CashBalanceModel, CashMovementModel, CashTrackedModel
from presentation.deps import get_current_user

router = APIRouter(prefix="/expenses/cash", tags=["expenses-cash"])

_HISTORY_LIMIT = 40


class CashAmountBody(BaseModel):
    amount: str = Field(min_length=1, max_length=40)
    note: str = Field(default="", max_length=500)


class CashMovementOut(BaseModel):
    id: int
    kind: str
    amount: str
    note: str
    balanceBefore: str | None
    balanceAfter: str
    createdByUserId: int
    createdAt: str
    text: str


class CashStateOut(BaseModel):
    balance: str | None
    balanceSet: bool
    history: list[CashMovementOut]


class CashChangeOut(BaseModel):
    balance: str
    message: str
    movement: CashMovementOut


def _norm(value: str | None) -> str:
    return (value or "").strip().lower().replace("ё", "е")


def is_cash_partner(user: dict) -> bool:
    role = _norm(user.get("role"))
    position = _norm(user.get("position"))
    return any(token in role or token in position for token in ("партнер", "partner"))


def require_cash_partner(user: dict = Depends(get_current_user)) -> dict:
    if not is_cash_partner(user):
        raise HTTPException(status_code=403, detail="Касса доступна только партнёрам")
    return user


def _money_or_none(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format_money(value)


def _movement_text(row: CashMovementModel) -> str:
    note = row.note or ""
    after = Decimal(row.balance_after)
    amount = Decimal(row.amount)
    before = None if row.balance_before is None else Decimal(row.balance_before)
    if row.kind == "expense" and before is not None:
        return cash_movement(before, "Потрачено", amount, after, note)
    if row.kind == "topup" and before is not None:
        return cash_movement(before, "Пополнение", amount, after, note)
    if row.kind == "set":
        if before is not None:
            return (
                f"Остаток в кассе: {format_money(before)}\n"
                f"Остаток установлен: {format_money(after)}\n\n"
                f"Остаток на текущий момент: {format_money(after)}"
            )
        return (
            f"Остаток установлен: {format_money(after)}\n\n"
            f"Остаток на текущий момент: {format_money(after)}"
        )
    return format_money(after)


def _movement_out(row: CashMovementModel) -> CashMovementOut:
    return CashMovementOut(
        id=row.id,
        kind=row.kind,
        amount=format_money(Decimal(row.amount)),
        note=row.note or "",
        balanceBefore=_money_or_none(None if row.balance_before is None else Decimal(row.balance_before)),
        balanceAfter=format_money(Decimal(row.balance_after)),
        createdByUserId=row.created_by_user_id,
        createdAt=row.created_at.isoformat(),
        text=_movement_text(row),
    )


async def _lock_balance(session: AsyncSession) -> CashBalanceModel:
    row = (
        await session.execute(
            select(CashBalanceModel).where(CashBalanceModel.id == 1).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        row = CashBalanceModel(id=1, balance=None, balance_set=False, updated_at=None)
        session.add(row)
        await session.flush()
    return row


def _parse_body_amount(raw: str) -> Decimal:
    amount = parse_amount(raw)
    if amount is None:
        raise HTTPException(status_code=422, detail="Отправьте сумму числом, например 298000")
    return amount


async def _history(session: AsyncSession) -> list[CashMovementOut]:
    rows = (
        await session.execute(
            select(CashMovementModel)
            .order_by(CashMovementModel.created_at.desc(), CashMovementModel.id.desc())
            .limit(_HISTORY_LIMIT)
        )
    ).scalars().all()
    return [_movement_out(row) for row in rows]


@router.get("", response_model=CashStateOut)
async def get_cash(
    user: dict = Depends(require_cash_partner),
    session: AsyncSession = Depends(get_session),
) -> CashStateOut:
    await sync_cash_reimbursements(session, int(user["id"]))
    await session.commit()
    row = (
        await session.execute(select(CashBalanceModel).where(CashBalanceModel.id == 1))
    ).scalar_one_or_none()
    if row is None or not row.balance_set or row.balance is None:
        return CashStateOut(balance=None, balanceSet=False, history=await _history(session))
    return CashStateOut(
        balance=format_money(Decimal(row.balance)),
        balanceSet=True,
        history=await _history(session),
    )


@router.post("/balance", response_model=CashChangeOut)
async def set_cash_balance(
    body: CashAmountBody,
    user: dict = Depends(require_cash_partner),
    session: AsyncSession = Depends(get_session),
) -> CashChangeOut:
    amount = _parse_body_amount(body.amount)
    row = await _lock_balance(session)
    before = None if not row.balance_set or row.balance is None else Decimal(row.balance)
    now = datetime.now(timezone.utc)
    row.balance = amount
    row.balance_set = True
    row.baseline_done = False
    row.balance_set_at = now
    row.updated_at = now
    await session.execute(delete(CashTrackedModel))
    movement = CashMovementModel(
        kind="set",
        amount=amount,
        note=" ".join((body.note or "").split()),
        balance_before=before,
        balance_after=amount,
        created_by_user_id=int(user["id"]),
        created_at=now,
    )
    session.add(movement)
    await session.flush()
    await sync_cash_reimbursements(session, int(user["id"]))
    await session.commit()
    await session.refresh(movement)
    return CashChangeOut(
        balance=format_money(Decimal(row.balance if row.balance is not None else amount)),
        message=_movement_text(movement),
        movement=_movement_out(movement),
    )


async def _move(
    session: AsyncSession,
    user: dict,
    body: CashAmountBody,
    *,
    expense: bool,
) -> CashChangeOut:
    amount = _parse_body_amount(body.amount)
    if amount <= 0:
        raise HTTPException(status_code=422, detail="Отправьте сумму больше нуля, например 50000")
    row = await _lock_balance(session)
    if not row.balance_set or row.balance is None:
        raise HTTPException(status_code=409, detail="Сначала задайте остаток")
    before = Decimal(row.balance)
    after = before - amount if expense else before + amount
    note = " ".join((body.note or "").split())
    now = datetime.now(timezone.utc)
    row.balance = after
    row.updated_at = now
    kind = "expense" if expense else "topup"
    movement = CashMovementModel(
        kind=kind,
        amount=amount,
        note=note,
        balance_before=before,
        balance_after=after,
        created_by_user_id=int(user["id"]),
        created_at=now,
    )
    session.add(movement)
    await session.commit()
    await session.refresh(movement)
    label = "Потрачено" if expense else "Пополнение"
    return CashChangeOut(
        balance=format_money(after),
        message=cash_movement(before, label, amount, after, note),
        movement=_movement_out(movement),
    )


@router.post("/expense", response_model=CashChangeOut)
async def record_cash_expense(
    body: CashAmountBody,
    user: dict = Depends(require_cash_partner),
    session: AsyncSession = Depends(get_session),
) -> CashChangeOut:
    return await _move(session, user, body, expense=True)


@router.post("/topup", response_model=CashChangeOut)
async def record_cash_topup(
    body: CashAmountBody,
    user: dict = Depends(require_cash_partner),
    session: AsyncSession = Depends(get_session),
) -> CashChangeOut:
    return await _move(session, user, body, expense=False)
