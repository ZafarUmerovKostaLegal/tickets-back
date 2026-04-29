

from __future__ import annotations

import json
import logging
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


def _rule_matches(
    if_block: dict,
    *,
    department_id: str | None,
    expense_type: str | None,
    project_id: str | None,
    is_reimbursable: bool,
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
    return True


def _coerce_to_list(v: object) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


def resolve_expense_notify_recipients(
    settings: Settings,
    *,
    department_id: str | None,
    expense_type: str | None,
    project_id: str | None,
    is_reimbursable: bool,
) -> list[str]:

    raw = (settings.expense_notify_routing_json or "").strip()
    if raw.startswith("\ufeff"):
        raw = raw.lstrip("\ufeff").strip()
    if not raw:
        return _dedupe_preserve(_parse_csv_emails(settings.expense_notify_to))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        _log.warning("EXPENSE_NOTIFY_ROUTING_JSON: невалидный JSON (%s), используем EXPENSE_NOTIFY_TO", e)
        return _dedupe_preserve(_parse_csv_emails(settings.expense_notify_to))

    if not isinstance(data, dict):
        _log.warning("EXPENSE_NOTIFY_ROUTING_JSON: ожидается объект, используем EXPENSE_NOTIFY_TO")
        return _dedupe_preserve(_parse_csv_emails(settings.expense_notify_to))

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
        ):
            continue
        to = _coerce_to_list(rule.get("to"))
        to = _dedupe_preserve(to)
        if to:
            _log.info(
                "expense notify: маршрутизация — правило #%s (if=%s) → %s",
                idx,
                if_block,
                to,
            )
            return to

    default = _coerce_to_list(data.get("default"))
    default = _dedupe_preserve(default)
    if default:
        _log.info("expense notify: маршрутизация — default → %s", default)
        return default

    return _dedupe_preserve(_parse_csv_emails(settings.expense_notify_to))
