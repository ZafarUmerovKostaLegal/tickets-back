

from __future__ import annotations

from typing import Any, Optional

RBAC_UI_VERSION = 1


MAIN_ADMIN = "Главный администратор"
ADMIN = "Администратор"
PARTNER = "Партнер"
IT = "IT отдел"
OFFICE_MGR_SPACE = "Офис менеджер"
OFFICE_MGR_HYPHEN = "Офис-менеджер"
EMPLOYEE = "Сотрудник"

USERS_VIEW_DIRECTORY: frozenset[str] = frozenset(
    {
        MAIN_ADMIN,
        ADMIN,
        PARTNER,
        IT,
        OFFICE_MGR_SPACE,
        OFFICE_MGR_HYPHEN,
    }
)

USERS_MANAGE_BLOCK_ARCHIVE: frozenset[str] = frozenset({MAIN_ADMIN, ADMIN, PARTNER})


USERS_ASSIGN_ORG_ROLES: frozenset[str] = frozenset({MAIN_ADMIN, ADMIN})

TICKETS_CROSS_USER: frozenset[str] = frozenset(
    {
        IT,
        ADMIN,
        MAIN_ADMIN,
        OFFICE_MGR_SPACE,
        OFFICE_MGR_HYPHEN,
    }
)

ATTENDANCE_VIEW: frozenset[str] = frozenset(
    {
        MAIN_ADMIN,
        ADMIN,
        PARTNER,
        IT,
        OFFICE_MGR_SPACE,
        OFFICE_MGR_HYPHEN,
        EMPLOYEE,
    }
)

ATTENDANCE_WORKDAY_WRITE: frozenset[str] = frozenset(
    {
        MAIN_ADMIN,
        ADMIN,
        PARTNER,
        IT,
        OFFICE_MGR_SPACE,
        OFFICE_MGR_HYPHEN,
    }
)

ATTENDANCE_HIKVISION_MAPPINGS: frozenset[str] = frozenset(
    {
        MAIN_ADMIN,
        ADMIN,
        PARTNER,
        OFFICE_MGR_SPACE,
        OFFICE_MGR_HYPHEN,
    }
)

VACATION_VIEW: frozenset[str] = frozenset(
    {
        MAIN_ADMIN,
        ADMIN,
        PARTNER,
        IT,
        OFFICE_MGR_SPACE,
        OFFICE_MGR_HYPHEN,
        EMPLOYEE,
    }
)

VACATION_MANAGE_SCHEDULE: frozenset[str] = frozenset(
    {
        MAIN_ADMIN,
        ADMIN,
        PARTNER,
        OFFICE_MGR_SPACE,
        OFFICE_MGR_HYPHEN,
    }
)

EXPENSES_VIEW: frozenset[str] = frozenset(
    {
        MAIN_ADMIN,
        ADMIN,
        PARTNER,
        IT,
        OFFICE_MGR_SPACE,
        EMPLOYEE,
    }
)

EXPENSES_MODERATE: frozenset[str] = frozenset({MAIN_ADMIN, ADMIN, PARTNER})

EXPENSES_ADMIN_EDIT: frozenset[str] = frozenset({MAIN_ADMIN, ADMIN})

TIME_TRACKING_VIEW_DIRECTORY: frozenset[str] = frozenset(
    {
        MAIN_ADMIN,
        ADMIN,
        PARTNER,
        IT,
        OFFICE_MGR_SPACE,
    }
)

TIME_TRACKING_MANAGE_ORG: frozenset[str] = frozenset({MAIN_ADMIN, ADMIN, PARTNER})

_VIEW_TIME_ENTRIES: frozenset[str] = frozenset(
    {
        MAIN_ADMIN,
        ADMIN,
        PARTNER,
        IT,
        OFFICE_MGR_SPACE,
    }
)

_MANAGE_TIME_ENTRIES: frozenset[str] = frozenset({MAIN_ADMIN, ADMIN, PARTNER})

INVENTORY_WRITE: frozenset[str] = frozenset({IT, ADMIN, OFFICE_MGR_SPACE, PARTNER})


NOTIFICATIONS_WRITE: frozenset[str] = frozenset({PARTNER, IT, OFFICE_MGR_SPACE, OFFICE_MGR_HYPHEN})

HOURLY_VIEW: frozenset[str] = frozenset(
    {
        MAIN_ADMIN,
        ADMIN,
        PARTNER,
        IT,
        OFFICE_MGR_SPACE,
    }
)

HOURLY_MANAGE: frozenset[str] = frozenset({MAIN_ADMIN, ADMIN, PARTNER})

HOURLY_ADMIN_RATES: frozenset[str] = frozenset({MAIN_ADMIN, ADMIN})


def normalize_role_key(role: str) -> str:

    return (role or "").strip().lower().replace("ё", "е")


def role_in_set(role: str, allowed: frozenset[str]) -> bool:
    rk = normalize_role_key(role)
    if not rk:
        return False
    for a in allowed:
        if normalize_role_key(a) == rk:
            return True
    return False


def build_ui_permissions(
    org_role: Optional[str],
    time_tracking_role: Optional[str],
) -> dict[str, Any]:

    r = org_role
    tt = (time_tracking_role or "").strip().lower()
    is_tt_user = tt == "user"
    is_tt_manager = tt == "manager"

    caps: dict[str, Any] = {
        "v": RBAC_UI_VERSION,
        "can_view_user_directory": role_in_set(r, USERS_VIEW_DIRECTORY),
        "can_manage_users_block_archive": role_in_set(r, USERS_MANAGE_BLOCK_ARCHIVE),
        "can_assign_org_roles": role_in_set(r, USERS_ASSIGN_ORG_ROLES),
        "can_assign_main_administrator_role": normalize_role_key(r) == normalize_role_key(MAIN_ADMIN),
        "tickets_can_view_all_org": role_in_set(r, TICKETS_CROSS_USER),
        "attendance_can_view": role_in_set(r, ATTENDANCE_VIEW),
        "attendance_can_edit_workday_settings": role_in_set(r, ATTENDANCE_WORKDAY_WRITE),
        "attendance_can_manage_hikvision_mappings": role_in_set(r, ATTENDANCE_HIKVISION_MAPPINGS),
        "vacation_can_view": role_in_set(r, VACATION_VIEW),
        "vacation_can_manage_schedule": role_in_set(r, VACATION_MANAGE_SCHEDULE),
        "expenses_can_view": role_in_set(r, EXPENSES_VIEW),
        "expenses_can_moderate": role_in_set(r, EXPENSES_MODERATE),
        "expenses_can_admin_edit": role_in_set(r, EXPENSES_ADMIN_EDIT),
        "time_tracking_can_view_directory": role_in_set(r, TIME_TRACKING_VIEW_DIRECTORY),
        "time_tracking_can_manage_org_users": role_in_set(r, TIME_TRACKING_MANAGE_ORG),
        "time_tracking_can_view_time_entries_scope": role_in_set(r, _VIEW_TIME_ENTRIES),
        "time_tracking_can_manage_time_entries_scope": role_in_set(r, _MANAGE_TIME_ENTRIES),
        "time_tracking_is_tt_user": is_tt_user,
        "time_tracking_is_tt_manager": is_tt_manager,
        "inventory_can_write": role_in_set(r, INVENTORY_WRITE),
        "notifications_can_write": role_in_set(r, NOTIFICATIONS_WRITE),
        "hourly_rates_can_view": role_in_set(r, HOURLY_VIEW),
        "hourly_rates_can_manage": role_in_set(r, HOURLY_MANAGE),
        "hourly_rates_admin_only_operations": role_in_set(r, HOURLY_ADMIN_RATES),
    }
    return caps
