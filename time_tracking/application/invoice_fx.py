"""FX conversion for invoice lines (project/expense source → invoice currency)."""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable

import httpx
from fastapi import HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models_invoices import InvoiceFxRateModel

logger = logging.getLogger(__name__)

_Q4 = Decimal("0.0001")
_Q8 = Decimal("0.00000001")
_ZERO = Decimal(0)
_CBU_JSON_PATH = "/ru/arkhiv-kursov-valyut/json"
_CBU_ORIGIN_DEFAULT = "https://cbu.uz"


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


def _parse_expense_date(raw: Any, fallback: date) -> date:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    s = str(raw or "").strip()[:10]
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            return date.fromisoformat(s)
        except ValueError:
            pass
    return fallback


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

    def has_pair_covering(self, from_ccy: str, to_ccy: str, on_date: date) -> bool:
        """True if a direct or inverse pair can resolve without cross-currency."""
        a, b = _norm_ccy(from_ccy), _norm_ccy(to_ccy)
        if a == b:
            return True
        if self._lookup_direct(a, b, on_date) is not None:
            return True
        inv = self._lookup_direct(b, a, on_date)
        return inv is not None and inv > 0

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
                "Курс должен подгрузиться с ЦБ РУз (cbu.uz) по дате расхода/работы."
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


def convert_expense_amount(
    book: FxRateBook,
    row: dict[str, Any],
    invoice_currency: str,
    fallback_date: date,
) -> FxConversion:
    """Convert expense amount into invoice currency by **expense date** (not issue date).

    Expenses are stored in UZS. If the invoice/project currency is UZS, the UZS
    amount is copied as-is (no FX). Otherwise UZS → invoice ccy via CBU/fx book
    on the expense date. Falls back to stored USD equivalent only when amount_uzs
    is missing.
    """
    inv_ccy = _norm_ccy(invoice_currency)
    on = _parse_expense_date(
        row.get("expense_date") if row.get("expense_date") is not None else row.get("expenseDate"),
        fallback_date,
    )
    uzs = _d(row.get("amount_uzs", row.get("amountUzs", 0)) or 0)
    if uzs > 0:
        if inv_ccy == "UZS":
            src = _money4(uzs)
            return FxConversion(
                source_amount=src,
                source_currency="UZS",
                target_currency="UZS",
                fx_rate=Decimal(1),
                converted_amount=src,
            )
        return convert_or_same(book, uzs, "UZS", inv_ccy, on)
    usd = _d(row.get("equivalent_amount", row.get("equivalentAmount", 0)) or 0)
    if inv_ccy == "USD":
        src = _money4(usd)
        return FxConversion(
            source_amount=src,
            source_currency="USD",
            target_currency="USD",
            fx_rate=Decimal(1),
            converted_amount=src,
        )
    return convert_or_same(book, usd, "USD", inv_ccy, on)


def fx_pairs_for_conversion(from_ccy: str, to_ccy: str) -> list[tuple[str, str]]:
    """Minimal FX pairs to seed for from→to (empty when same currency)."""
    a, b = _norm_ccy(from_ccy), _norm_ccy(to_ccy)
    if a == b:
        return []
    pairs: list[tuple[str, str]] = [(a, b), (b, a)]
    if a != "USD" and b != "USD":
        pairs.extend([("USD", a), (a, "USD"), ("USD", b), (b, "USD")])
    if a == "UZS" or b == "UZS" or a != "USD" or b != "USD":
        pairs.extend([("USD", "UZS"), ("UZS", "USD")])
    # de-dupe preserving order
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for p in pairs:
        if p[0] == p[1] or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _cbu_origin() -> str:
    return (os.getenv("CBU_ORIGIN") or _CBU_ORIGIN_DEFAULT).rstrip("/")


async def _fetch_cbu_uzs_per_unit(iso_date: str) -> dict[str, Decimal]:
    """Return map CCY → UZS per 1 unit of CCY for the given ISO date.

    Tries exact archive day, then previous calendar days (CBU often publishes
    «с даты N» a day ahead), then the latest list.
    """
    base = _cbu_origin()
    try:
        anchor = date.fromisoformat(iso_date[:10])
    except ValueError:
        anchor = date.today()
    urls: list[str] = []
    for back in range(0, 8):
        d = date.fromordinal(anchor.toordinal() - back)
        urls.append(f"{base}{_CBU_JSON_PATH}/all/{d.isoformat()}/")
    urls.append(f"{base}{_CBU_JSON_PATH}/")
    last_err: Exception | None = None
    async with httpx.AsyncClient(timeout=20.0) as client:
        for url in urls:
            try:
                res = await client.get(url, headers={"Accept": "application/json"})
                res.raise_for_status()
                rows = res.json()
                if not isinstance(rows, list) or not rows:
                    raise ValueError("empty CBU rates list")
                out: dict[str, Decimal] = {}
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    ccy = str(r.get("Ccy") or "").strip().upper()
                    if not ccy:
                        continue
                    try:
                        nom = Decimal(str(r.get("Nominal") or "1").replace(",", "."))
                        rate = Decimal(str(r.get("Rate") or "0").replace(",", "."))
                    except Exception:
                        continue
                    if nom <= 0 or rate <= 0:
                        continue
                    out[ccy] = (rate / nom).quantize(_Q8, rounding=ROUND_HALF_UP)
                if "USD" not in out or out["USD"] <= 0:
                    raise ValueError("CBU response missing USD")
                return out
            except Exception as exc:
                last_err = exc
                continue
    raise HTTPException(
        status_code=502,
        detail=f"Не удалось загрузить курсы ЦБ РУз на {iso_date}: {last_err}",
    )


async def upsert_fx_rates_payload(
    session: AsyncSession,
    rates: Iterable[dict[str, Any]],
) -> int:
    """Upsert explicit FX rows from client/CBU (1 from = rate to on rate_date)."""
    written = 0
    for item in rates:
        if not isinstance(item, dict):
            continue
        from_ccy = str(item.get("fromCurrency") or item.get("from_currency") or "").strip()
        to_ccy = str(item.get("toCurrency") or item.get("to_currency") or "").strip()
        raw_date = str(item.get("rateDate") or item.get("rate_date") or "").strip()[:10]
        try:
            rate_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        try:
            rate = Decimal(str(item.get("rate") or "0").replace(",", "."))
        except Exception:
            continue
        if rate <= 0 or not from_ccy or not to_ccy:
            continue
        await _upsert_fx_pair(session, from_ccy, to_ccy, rate_date, rate)
        written += 1
    if written:
        await session.flush()
    return written


async def _upsert_fx_pair(
    session: AsyncSession,
    from_ccy: str,
    to_ccy: str,
    rate_date: date,
    rate: Decimal,
) -> None:
    a, b = _norm_ccy(from_ccy), _norm_ccy(to_ccy)
    if a == b or rate <= 0:
        return
    existing = (
        await session.execute(
            select(InvoiceFxRateModel).where(
                and_(
                    InvoiceFxRateModel.from_currency == a,
                    InvoiceFxRateModel.to_currency == b,
                    InvoiceFxRateModel.rate_date == rate_date,
                )
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.rate = rate
        return
    session.add(
        InvoiceFxRateModel(
            id=str(uuid.uuid4()),
            from_currency=a,
            to_currency=b,
            rate_date=rate_date,
            rate=rate,
            created_at=datetime.now(timezone.utc),
        )
    )


async def seed_cbu_fx_rates_for_date(session: AsyncSession, on_date: date) -> int:
    """Fetch CBU rates for on_date and upsert USD↔UZS (+ other major pairs via UZS)."""
    uzs_per = await _fetch_cbu_uzs_per_unit(on_date.isoformat())
    written = 0
    for ccy, uzs_per_unit in uzs_per.items():
        if ccy == "UZS" or uzs_per_unit <= 0:
            continue
        # 1 CCY = uzs_per_unit UZS
        await _upsert_fx_pair(session, ccy, "UZS", on_date, uzs_per_unit)
        await _upsert_fx_pair(
            session,
            "UZS",
            ccy,
            on_date,
            (Decimal(1) / uzs_per_unit).quantize(_Q8, rounding=ROUND_HALF_UP),
        )
        written += 2
        # Also store CCY↔USD via cross when not USD
        if ccy != "USD":
            uzs_usd = uzs_per["USD"]
            # 1 CCY = uzs_per_unit/uzs_usd USD
            ccy_usd = (uzs_per_unit / uzs_usd).quantize(_Q8, rounding=ROUND_HALF_UP)
            if ccy_usd > 0:
                await _upsert_fx_pair(session, ccy, "USD", on_date, ccy_usd)
                await _upsert_fx_pair(
                    session,
                    "USD",
                    ccy,
                    on_date,
                    (Decimal(1) / ccy_usd).quantize(_Q8, rounding=ROUND_HALF_UP),
                )
                written += 2
    await session.flush()
    return written


async def ensure_fx_book_for_dates(
    session: AsyncSession,
    dates: Iterable[date | None],
    *,
    required_pairs: Iterable[tuple[str, str]] | None = None,
) -> FxRateBook:
    """Load FX book; if required pairs missing for a date, seed from CBU.

    Pass only the dates that conversions actually need (expense dates / work dates).
    Same-currency pairs are ignored — no CBU call.
    """
    unique = sorted({d for d in dates if isinstance(d, date)})
    raw_pairs = list(required_pairs) if required_pairs is not None else [("USD", "UZS"), ("UZS", "USD")]
    pairs = [( _norm_ccy(a), _norm_ccy(b) ) for a, b in raw_pairs if _norm_ccy(a) != _norm_ccy(b)]
    if not unique or not pairs:
        return await load_fx_rate_book(session)
    book = await load_fx_rate_book(session)
    seeded_any = False
    for d in unique:
        need_seed = False
        for a, b in pairs:
            if not book.has_pair_covering(a, b, d):
                need_seed = True
                break
            try:
                book.rate(a, b, d)
            except HTTPException:
                need_seed = True
                break
        if not need_seed:
            continue
        try:
            n = await seed_cbu_fx_rates_for_date(session, d)
            logger.info("Seeded %s CBU FX rows for %s", n, d.isoformat())
            seeded_any = True
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("CBU FX seed failed for %s: %s", d.isoformat(), exc)
            raise HTTPException(
                status_code=502,
                detail=f"Не удалось загрузить курсы ЦБ РУз на {d.isoformat()}: {exc}",
            ) from exc
    if seeded_any:
        book = await load_fx_rate_book(session)
    return book
