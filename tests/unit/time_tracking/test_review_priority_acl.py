from application.reports.partner_scope import viewer_can_view_all_pending_partner_confirmations


def _can_set_review_priority(viewer: dict, submitted_by: int) -> bool:
    """Зеркало ACL PATCH: отправитель или менеджер/админ полного списка."""
    vid = int(viewer["id"])
    is_submitter = vid == int(submitted_by)
    can_manage = viewer_can_view_all_pending_partner_confirmations(viewer)
    return is_submitter or can_manage


def test_submitter_can_set_priority():
    viewer = {"id": 42, "time_tracking_role": "employee", "role": "Сотрудник", "permissions": {}}
    assert _can_set_review_priority(viewer, 42) is True


def test_partner_non_submitter_cannot_set_priority():
    viewer_emp = {"id": 7, "time_tracking_role": "employee", "role": "Сотрудник", "permissions": {}}
    assert _can_set_review_priority(viewer_emp, 42) is False


def test_manager_can_set_priority():
    viewer = {"id": 1, "time_tracking_role": "manager", "role": "Сотрудник", "permissions": {}}
    assert _can_set_review_priority(viewer, 99) is True
