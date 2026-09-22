"""Cash reimbursements for the balance bot. Shared secret, not a user session."""

from __future__ import annotations

import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.config import get_settings
from infrastructure.database import get_session

router = APIRouter(prefix="/expenses", tags=["expenses"])

_FEED_SQL = text(
    """
    SELECT id,
           amount_uzs,
           coalesce(description, '') AS description,
           paid_at,
           coalesce(expense_type, '') AS expense_type
    FROM expense_requests
    WHERE status = 'paid'
      AND lower(coalesce(payment_method, '')) = 'cash'
      AND coalesce(expense_type, '') <> 'partner_expense'
    ORDER BY paid_at ASC NULLS LAST, id ASC
    """
)


def bot_token_ok(expected: str, presented: str) -> bool:
    left = (expected or "").strip()
    right = (presented or "").strip()
    if not left or not right:
        return False
    return hmac.compare_digest(left, right)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


@router.get("/reimbursed-feed")
async def reimbursed_feed(
    session: AsyncSession = Depends(get_session),
    x_expenses_bot_token: str | None = Header(None, alias="X-Expenses-Bot-Token"),
):
    settings = get_settings()
    if not bot_token_ok(settings.expenses_bot_token, x_expenses_bot_token or ""):
        raise HTTPException(status_code=401, detail="Unauthorized")
    result = await session.execute(_FEED_SQL)
    items = [
        {
            "id": row.id,
            "status": "paid",
            "paymentMethod": "cash",
            "expenseType": row.expense_type,
            "amountUzs": str(row.amount_uzs),
            "description": row.description or "",
            "paidAt": _iso(row.paid_at),
        }
        for row in result
    ]
    return {"items": items, "total": len(items)}
