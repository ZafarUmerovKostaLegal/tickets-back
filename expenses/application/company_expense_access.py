"""Private company expenses: only three people create, approve and get mail. Partners may see the list."""

from __future__ import annotations

COMPANY_EXPENSE_TYPE = "company_expense"

COMPANY_EXPENSE_ACTOR_EMAILS = (
    "oidrisova@kostalegal.com",
    "zumerov@kostalegal.com",
    "aakhmadjonov@kostalegal.com",
)
_ACTORS = frozenset(COMPANY_EXPENSE_ACTOR_EMAILS)


def _email(user: dict | None) -> str:
    if not isinstance(user, dict):
        return ""
    return str(user.get("email") or "").strip().lower()


def _is_partner(user: dict | None) -> bool:
    raw = user or {}
    blob = f"{raw.get('role') or ''} {raw.get('position') or ''}".strip().lower().replace("ё", "е")
    return "партнер" in blob or "partner" in blob


def is_company_expense(expense_type: str | None) -> bool:
    return (expense_type or "").strip() == COMPANY_EXPENSE_TYPE


def can_act_on_company_expense(user: dict | None) -> bool:
    return _email(user) in _ACTORS


def can_view_company_expense(user: dict | None) -> bool:
    return can_act_on_company_expense(user) or _is_partner(user)
