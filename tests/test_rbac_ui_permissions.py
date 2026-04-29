from backend_common.rbac_ui_permissions import (
    MAIN_ADMIN,
    build_ui_permissions,
    role_in_set,
    NOTIFICATIONS_WRITE,
)


def test_role_in_set_hyphen_office_manager():
    assert role_in_set("Офис-менеджер", NOTIFICATIONS_WRITE)
    assert role_in_set("Офис менеджер", NOTIFICATIONS_WRITE)


def test_build_ui_permissions_tt_manager():
    p = build_ui_permissions("Сотрудник", "manager")
    assert p["time_tracking_is_tt_manager"] is True
    assert p["time_tracking_is_tt_user"] is False


def test_build_ui_permissions_main_admin():
    p = build_ui_permissions(MAIN_ADMIN, None)
    assert p["can_assign_main_administrator_role"] is True
    assert p["can_assign_org_roles"] is True
