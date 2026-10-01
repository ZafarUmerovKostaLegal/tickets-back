from support.service_path import ensure_service_in_path


def test_admins_and_partners_can_open_hr():
    ensure_service_in_path("hr")
    from infrastructure.access import can_access_hr

    assert can_access_hr("Администратор")
    assert can_access_hr("Главный администратор")
    assert can_access_hr("Партнёр")
    assert can_access_hr("Юрист", "Partner")


def test_other_roles_cannot_open_hr():
    ensure_service_in_path("hr")
    from infrastructure.access import can_access_hr

    assert not can_access_hr("Сотрудник")
    assert not can_access_hr("Юрист")
    assert not can_access_hr(None)
