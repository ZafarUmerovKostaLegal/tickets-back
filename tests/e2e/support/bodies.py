from __future__ import annotations

import re
from typing import Any

_PATH_PARAM = re.compile(r"\{[^}]+\}")


def fill_path_for_body(path: str) -> str:
    return _PATH_PARAM.sub("1", path)


def request_body(method: str, path: str) -> Any | None:
    if method not in {"POST", "PUT", "PATCH"}:
        return None
    p = fill_path_for_body(path)

    if "/time-tracking/users" in p and method == "POST":
        return {"auth_user_id": 1, "email": "e2e@test.local", "role": "user"}
    if "/time-tracking/time-entries" in p and method == "POST":
        return {
            "auth_user_id": 1,
            "project_id": "proj-1",
            "task_id": "task-1",
            "work_date": "2026-01-15",
            "hours": "1.0",
        }
    if "/tickets" in p and method == "POST" and p.rstrip("/").endswith("/tickets"):
        return {"title": "E2E", "description": "test", "priority": "Низкий"}
    if "/expenses" in p and method == "POST":
        return {"amount": "100.00", "currency": "USD", "expense_date": "2026-01-15"}
    if "/correspondence" in p and "incoming" in p:
        return {"partner_user_id": 1, "doc_type": "letter", "subject": "E2E"}
    if "/vacations/leave-requests" in p and method == "POST":
        return {"leave_kind_id": 1, "date_from": "2026-08-01", "date_to": "2026-08-05"}
    if "/chat/" in p and "/messages" in p and method == "POST":
        return {"body": "e2e"}
    if "/reports/partner-confirmations" in p:
        return {"project_id": "p1", "week_start": "2026-01-06"}
    if method == "PATCH":
        return {"display_name": "E2E Patch"}
    return {}
