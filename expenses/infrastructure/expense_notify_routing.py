

from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infrastructure.config import Settings

_log = logging.getLogger(__name__)


def _parse_csv_emails(raw: str) -> list[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def _dedupe_preserve(emails: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for e in emails:
        k = e.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def _norm_str(v: str | None) -> str:
    return (v or "").strip()


def _as_decimal(v: object) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace(" ", "").replace(",", ""))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _rule_matches(
    if_block: dict,
    *,
    department_id: str | None,
    expense_type: str | None,
    project_id: str | None,
    is_reimbursable: bool,
    amount_uzs: Decimal | None,
) -> bool:
    if not if_block:
        return False
    if "departmentId" in if_block:
        if _norm_str(if_block.get("departmentId")) != _norm_str(department_id):
            return False
    if "expenseType" in if_block:
        if _norm_str(if_block.get("expenseType")) != _norm_str(expense_type):
            return False
    if "projectId" in if_block:
        if _norm_str(if_block.get("projectId")) != _norm_str(project_id):
            return False
    if "isReimbursable" in if_block:
        want = if_block.get("isReimbursable")
        if isinstance(want, bool):
            if want != is_reimbursable:
                return False
        elif isinstance(want, str):
            low = want.strip().lower()
            if low in ("true", "1", "yes"):
                if not is_reimbursable:
                    return False
            elif low in ("false", "0", "no"):
                if is_reimbursable:
                    return False
            else:
                return False
        else:
            return False

    amount_max = _as_decimal(if_block.get("amountUzsMax")) if "amountUzsMax" in if_block else None
    amount_min = _as_decimal(if_block.get("amountUzsMin")) if "amountUzsMin" in if_block else None
    if amount_max is not None or amount_min is not None:
        if amount_uzs is None:
            return False
        if amount_max is not None and amount_uzs > amount_max:
            return False
        if amount_min is not None and amount_uzs < amount_min:
            return False
    return True


def _coerce_to_list(v: object) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


def _resolve_simple_amount_tier(
    settings: Settings,
    *,
    amount_uzs: Decimal | None,
) -> list[str] | None:
    """
    Простой режим без ROUTING_JSON:
    - сумма <= EXPENSE_APPROVAL_LOW_LIMIT_UZS → EXPENSE_NOTIFY_TO_LOW
    - сумма > лимита → EXPENSE_NOTIFY_TO
    """
    low_limit = settings.expense_approval_low_limit_uzs
    to_low = _dedupe_preserve(_parse_csv_emails(settings.expense_notify_to_low))
    if low_limit is None or not to_low or amount_uzs is None:
        return None
    if amount_uzs <= low_limit:
        _log.info(
            "expense notify: low-limit tier (amount=%s <= %s) → %s",
            amount_uzs,
            low_limit,
            to_low,
        )
        return to_low
    return None


def resolve_expense_notify_recipients(
    settings: Settings,
    *,
    department_id: str | None,
    expense_type: str | None,
    project_id: str | None,
    is_reimbursable: bool,
    amount_uzs: Decimal | None = None,
) -> list[str]:

    raw = (settings.expense_notify_routing_json or "").strip()
    if raw.startswith("\ufeff"):
        raw = raw.lstrip("\ufeff").strip()
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            _log.warning("EXPENSE_NOTIFY_ROUTING_JSON: невалидный JSON (%s), используем EXPENSE_NOTIFY_TO", e)
            data = None

        if data is not None and not isinstance(data, dict):
            _log.warning("EXPENSE_NOTIFY_ROUTING_JSON: ожидается объект, используем EXPENSE_NOTIFY_TO")
            data = None

        if isinstance(data, dict):
            rules = data.get("rules")
            if not isinstance(rules, list):
                rules = []

            for idx, rule in enumerate(rules):
                if not isinstance(rule, dict):
                    continue
                if_block = rule.get("if")
                if not isinstance(if_block, dict):
                    continue
                if not _rule_matches(
                    if_block,
                    department_id=department_id,
                    expense_type=expense_type,
                    project_id=project_id,
                    is_reimbursable=is_reimbursable,
                    amount_uzs=amount_uzs,
                ):
                    continue
                to = _dedupe_preserve(_coerce_to_list(rule.get("to")))
                if to:
                    _log.info(
                        "expense notify: маршрутизация — правило #%s (if=%s) → %s",
                        idx,
                        if_block,
                        to,
                    )
                    return to

            default = _dedupe_preserve(_coerce_to_list(data.get("default")))
            if default:
                _log.info("expense notify: маршрутизация — default → %s", default)
                return default

    tier = _resolve_simple_amount_tier(settings, amount_uzs=amount_uzs)
    if tier is not None:
        return tier

    return _dedupe_preserve(_parse_csv_emails(settings.expense_notify_to))
