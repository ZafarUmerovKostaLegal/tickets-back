"""FX conversion for invoice lines (project/expense source → invoice currency)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models_invoices import InvoiceFxRateModel

_Q4 = Decimal("0.0001")
_Q8 = Decimal("0.00000001")
_ZERO = Decimal(0)


def _norm_ccy(v: str | None) -> str:
    return (v or "USD").strip().upper()[:10] or "USD"


def _money4(v: Decimal) -> Decimal:
    return v.quantize(_Q4, rounding=ROUND_HALF_UP)


def _d(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if v is None:
        return _ZERO
    return Decimal(str(v))


@dataclass(frozen=True)
class FxConversion:
    source_amount: Decimal
    source_currency: str
    target_currency: str
    fx_rate: Decimal  # 1 source = fx_rate target
    converted_amount: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "sourceAmount": float(self.source_amount),
            "sourceCurrency": self.source_currency,
            "targetCurrency": self.target_currency,
            "fxRate": float(self.fx_rate),
            "convertedAmount": float(self.converted_amount),
        }


class FxRateBook:
    """In-memory rate book: (from, to) → list of (rate_date, rate) sorted desc by date."""

    def __init__(self) -> None:
        self._pairs: dict[tuple[str, str], list[tuple[date, Decimal]]] = {}

    def add(self, from_ccy: str, to_ccy: str, rate_date: date, rate: Decimal) -> None:
        a, b = _norm_ccy(from_ccy), _norm_ccy(to_ccy)
        if a == b or rate <= 0:
            return
        key = (a, b)
        rows = self._pairs.setdefault(key, [])
        rows.append((rate_date, _d(rate)))
        rows.sort(key=lambda x: x[0], reverse=True)

    def _lookup_direct(self, from_ccy: str, to_ccy: str, on_date: date) -> Decimal | None:
        rows = self._pairs.get((from_ccy, to_ccy))
        if not rows:
            return None
        for rd, rate in rows:
            if rd <= on_date:
                return rate
        return None

    def rate(self, from_ccy: str, to_ccy: str, on_date: date) -> Decimal:
        a, b = _norm_ccy(from_ccy), _norm_ccy(to_ccy)
        if a == b:
            return Decimal(1)
        direct = self._lookup_direct(a, b, on_date)
        if direct is not None:
            return direct
        inv = self._lookup_direct(b, a, on_date)
        if inv is not None and inv > 0:
            return (Decimal(1) / inv).quantize(_Q8, rounding=ROUND_HALF_UP)
        # Cross via USD
        if a != "USD" and b != "USD":
            a_usd = self._lookup_direct(a, "USD", on_date)
            if a_usd is None:
                usd_a = self._lookup_direct("USD", a, on_date)
                if usd_a is not None and usd_a > 0:
                    a_usd = (Decimal(1) / usd_a).quantize(_Q8, rounding=ROUND_HALF_UP)
            usd_b = self._lookup_direct("USD", b, on_date)
            if usd_b is None:
                b_usd = self._lookup_direct(b, "USD", on_date)
                if b_usd is not None and b_usd > 0:
                    usd_b = (Decimal(1) / b_usd).quantize(_Q8, rounding=ROUND_HALF_UP)
            if a_usd is not None and usd_b is not None:
                return (a_usd * usd_b).quantize(_Q8, rounding=ROUND_HALF_UP)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Нет курса FX {a}→{b} на дату {on_date.isoformat()}. "
                "Добавьте курс в time_tracking_fx_rates (или UZS/USD и пару через USD)."
            ),
        )

    def convert(
        self,
        amount: Decimal,
        from_currency: str,
        to_currency: str,
        on_date: date,
    ) -> FxConversion:
        src = _money4(_d(amount))
        a, b = _norm_ccy(from_currency), _norm_ccy(to_currency)
        fx = self.rate(a, b, on_date)
        converted = _money4(src * fx) if a != b else src
        return FxConversion(
            source_amount=src,
            source_currency=a,
            target_currency=b,
            fx_rate=fx if a != b else Decimal(1),
            converted_amount=converted,
        )


async def load_fx_rate_book(session: AsyncSession) -> FxRateBook:
    book = FxRateBook()
    q = select(InvoiceFxRateModel).order_by(
        InvoiceFxRateModel.rate_date.desc(),
        InvoiceFxRateModel.from_currency,
        InvoiceFxRateModel.to_currency,
    )
    rows = list((await session.execute(q)).scalars().all())
    for r in rows:
        book.add(r.from_currency, r.to_currency, r.rate_date, _d(r.rate))
    return book


def convert_or_same(
    book: FxRateBook,
    amount: Decimal,
    from_currency: str,
    to_currency: str,
    on_date: date,
) -> FxConversion:
    return book.convert(amount, from_currency, to_currency, on_date)
