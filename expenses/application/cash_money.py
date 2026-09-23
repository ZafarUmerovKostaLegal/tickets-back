"""Cash amount parsing and movement text, matching the expenses bot."""

from decimal import Decimal, InvalidOperation

_MAX = Decimal("1000000000000000")
_Q = Decimal("0.01")


def format_money(value: Decimal) -> str:
    amount = Decimal(value).quantize(_Q)
    if amount == amount.to_integral_value():
        return format(amount.to_integral_value(), "f")
    return format(amount, "f")


def parse_amount(text: str) -> Decimal | None:
    raw = (text or "").strip().replace("\u00a0", "").replace(" ", "")
    if not raw:
        return None
    sign = ""
    if raw[0] in "+-":
        sign = "-" if raw[0] == "-" else ""
        raw = raw[1:]
    if not raw:
        return None

    if raw.count(",") > 1 and "." not in raw:
        raw = raw.replace(",", "")
    elif raw.count(".") > 1 and "," not in raw:
        raw = raw.replace(".", "")
    elif "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        left, right = raw.split(",", 1)
        if len(right) == 3 and right.isdigit() and left.isdigit():
            raw = left + right
        else:
            raw = f"{left}.{right}"

    try:
        value = Decimal(sign + raw)
    except InvalidOperation:
        return None
    if not value.is_finite() or abs(value) > _MAX:
        return None
    return value.quantize(_Q)


def cash_movement(before: Decimal, label: str, amount: Decimal, after: Decimal, detail: str = "") -> str:
    reason = " ".join((detail or "").split())
    spent = f"{label}: {format_money(amount)}"
    head = f"Остаток в кассе: {format_money(before)}"
    tail = f"Остаток на текущий момент: {format_money(after)}"
    if reason:
        spent = f"{spent} ({reason})"
    return f"{head}\n{spent}\n\n{tail}"
