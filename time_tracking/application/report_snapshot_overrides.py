

from __future__ import annotations

import json
from typing import Any


OVERRIDE_ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "workDate",
        "recordedAt",
        "clientName",
        "projectName",
        "taskName",
        "note",
        "description",
        "hours",
        "isBillable",
        "taskBillableByDefault",
        "employeeName",
        "employeePosition",
        "billableRate",
        "amountToPay",
        "costRate",
        "costAmount",
        "currency",
        "externalReferenceUrl",
    }
)


OVERRIDE_FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "timeEntryId",
        "clientId",
        "projectId",
        "projectCode",
        "taskId",
        "authUserId",
        "invoiceId",
        "invoiceNumber",
        "isInvoiced",
        "isPaid",
        "isWeekSubmitted",
        "sourceType",
        "sourceId",
        "sortOrder",
        "reportGroupBy",
        "reportGroupId",
    }
)


_SNAKE_TO_CAMEL: dict[str, str] = {
    "work_date": "workDate",
    "recorded_at": "recordedAt",
    "client_name": "clientName",
    "project_name": "projectName",
    "project_code": "projectCode",
    "task_name": "taskName",
    "is_billable": "isBillable",
    "task_billable_by_default": "taskBillableByDefault",
    "employee_name": "employeeName",
    "employee_position": "employeePosition",
    "billable_rate": "billableRate",
    "amount_to_pay": "amountToPay",
    "cost_rate": "costRate",
    "cost_amount": "costAmount",
    "external_reference_url": "externalReferenceUrl",
}


def _normalize_key(k: str) -> str:
    s = (k or "").strip()
    if s in _SNAKE_TO_CAMEL:
        return _SNAKE_TO_CAMEL[s]
    if "_" in s:
        parts = s.split("_")
        return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:] if p)
    return s


MAX_OVERRIDE_JSON_BYTES = 64 * 1024
MAX_TOP_LEVEL_KEYS = 32


def validate_and_normalize_overrides(raw: dict[str, Any] | None) -> dict[str, Any]:

    if raw is None or not raw:
        raise ValueError("Передайте непустой объект overrides")
    if not isinstance(raw, dict):
        raise TypeError("overrides должен быть объектом (JSON object)")
    if len(raw) > MAX_TOP_LEVEL_KEYS:
        raise ValueError("Слишком много полей в overrides")
    if len(json.dumps(raw, default=str)) > MAX_OVERRIDE_JSON_BYTES:
        raise ValueError("overrides слишком велики")

    out: dict[str, Any] = {}
    for k, v in raw.items():
        key = _normalize_key(str(k))
        if key in OVERRIDE_FORBIDDEN_KEYS:
            raise ValueError(
                f"Поле нельзя менять: {key!r} (запрещены id, ссылки, код проекта, системные флаги)"
            )
        if key not in OVERRIDE_ALLOWED_KEYS:
            raise ValueError(f"Поле нельзя менять: {key!r} (нет в списке разрешённых)")
        if v is not None and isinstance(v, (dict, list, tuple, set)):
            raise ValueError(f"Вложенные значения не поддерживаются: {key!r}")
        out[key] = v
    if not out:
        raise ValueError("После проверки overrides пусты")
    return out


def merge_frozen_and_overrides(frozen: dict[str, Any] | None, ovr: dict[str, Any] | None) -> dict[str, Any]:

    base: dict[str, Any] = dict(frozen) if isinstance(frozen, dict) else {}
    if ovr and isinstance(ovr, dict):
        base.update(ovr)
    return base
