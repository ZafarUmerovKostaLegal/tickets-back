from backend_common.rbac_ui_permissions import (
    MAIN_ADMIN,
    build_ui_permissions,
    role_in_set,
    NOTIFICATIONS_WRITE,
)


def test_role_in_set_hyphen_office_manager():
    assert role_in_set("Офис-менеджер", NOTIFICATIONS_WRITE)
    assert role_in_set("Офис менеджер", NOTIFICATIONS_WRITE)


from backend_common.tt_position_access import position_has_tt_full_ops_no_reports


def test_build_ui_permissions_tt_manager():
    p = build_ui_permissions("Сотрудник", "manager")
    assert p["time_tracking_is_tt_manager"] is True
    assert p["time_tracking_is_tt_user"] is False


def test_build_ui_permissions_bdm_full_ops_no_reports():
    p = build_ui_permissions("Сотрудник", "manager", "Business Development Manager")
    assert p["time_tracking_can_manage_org_users"] is True
    assert p["time_tracking_can_view_time_entries_scope"] is True
    assert p["hourly_rates_can_manage"] is True
    assert p["time_tracking_can_view_reports"] is False


def test_build_ui_permissions_accountant_position():
    assert position_has_tt_full_ops_no_reports("Accountant")
    p = build_ui_permissions("Сотрудник", "user", "Accountant")
    assert p["time_tracking_can_view_reports"] is False
    assert p["hourly_rates_can_view"] is True
    assert p["vacation_can_manage_schedule"] is True


def test_build_ui_permissions_bdm_vacation_manage():
    p = build_ui_permissions("Сотрудник", "manager", "Contracts and BD Assistant")
    assert p["vacation_can_manage_schedule"] is True


def test_build_ui_permissions_main_admin():
    p = build_ui_permissions(MAIN_ADMIN, None)
    assert p["can_assign_main_administrator_role"] is True
    assert p["can_assign_org_roles"] is True
