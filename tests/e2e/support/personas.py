from __future__ import annotations

from typing import Any

_BASE_USER: dict[str, Any] = {
    "picture": None,
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": None,
    "position": None,
    "desktop_background": None,
    "initials": None,
}

EMPLOYEE: dict[str, Any] = {
    **_BASE_USER,
    "id": 10,
    "email": "employee@test.local",
    "display_name": "Employee E2E",
    "role": "Сотрудник",
    "time_tracking_role": "user",
    "permissions": {"time_tracking_can_view_reports": True},
    "is_blocked": False,
    "is_archived": False,
}

MANAGER: dict[str, Any] = {
    **EMPLOYEE,
    "id": 11,
    "email": "manager@test.local",
    "display_name": "Manager E2E",
    "time_tracking_role": "manager",
}

ADMIN: dict[str, Any] = {
    **EMPLOYEE,
    "id": 12,
    "email": "admin@test.local",
    "display_name": "Admin E2E",
    "role": "Администратор",
    "time_tracking_role": "manager",
    "permissions": {
        "time_tracking_can_view_reports": True,
        "time_tracking_can_manage_org_users": True,
    },
}

PARTNER: dict[str, Any] = {
    **EMPLOYEE,
    "id": 13,
    "email": "partner@test.local",
    "display_name": "Partner E2E",
    "role": "Партнер",
    "time_tracking_role": "user",
}
