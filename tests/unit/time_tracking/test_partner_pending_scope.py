import pytest
from fastapi import HTTPException

from application.reports.partner_scope import (              
    normalize_partner_pending_scope,
    pending_confirmation_visible_for_user_mine,
    viewer_can_view_all_pending_partner_confirmations,
)


def _viewer(**kwargs):
    base = {
        "id": 1,
        "role": "Сотрудник",
        "time_tracking_role": "user",
        "permissions": {},
    }
    base.update(kwargs)
    return base


class _Sig:
    def __init__(self, partner_auth_user_id: int):
        self.partner_auth_user_id = partner_auth_user_id


class _Req:
    def __init__(self, *, status: str, signatures: list[_Sig] | None = None):
        self.status = status
        self.signatures = signatures or []


def test_viewer_can_view_all_pending_tt_manager():
    assert viewer_can_view_all_pending_partner_confirmations(
        _viewer(time_tracking_role="manager")
    )


def test_viewer_can_view_all_pending_admin():
    assert viewer_can_view_all_pending_partner_confirmations(
        _viewer(role="Администратор")
    )


def test_viewer_can_view_all_pending_manage_org_users_flag():
    assert viewer_can_view_all_pending_partner_confirmations(
        _viewer(permissions={"time_tracking_can_manage_org_users": True})
    )


def test_viewer_cannot_view_all_pending_regular_employee():
    assert not viewer_can_view_all_pending_partner_confirmations(_viewer())


def test_viewer_cannot_view_all_pending_it_role():
    assert not viewer_can_view_all_pending_partner_confirmations(
        _viewer(role="IT отдел")
    )


def test_normalize_pending_scope_defaults():
    assert normalize_partner_pending_scope(None) == "mine"
    assert normalize_partner_pending_scope("") == "mine"
    assert normalize_partner_pending_scope("mine") == "mine"
    assert normalize_partner_pending_scope("ALL") == "all"


def test_normalize_pending_scope_invalid():
    with pytest.raises(HTTPException) as exc:
        normalize_partner_pending_scope("foo")
    assert exc.value.status_code == 400


def test_pending_visible_for_user_mine_required_partner():
    req = _Req(status="pending_partners")
    assert pending_confirmation_visible_for_user_mine(
        req, required_partners=[10, 11], viewer_id=10
    )


def test_pending_visible_for_user_mine_signed_waiting_for_others():
    req = _Req(status="pending_partners", signatures=[_Sig(10)])
    assert pending_confirmation_visible_for_user_mine(
        req, required_partners=[10, 11], viewer_id=10
    )


def test_pending_visible_for_user_mine_not_participant():
    req = _Req(status="pending_partners")
    assert not pending_confirmation_visible_for_user_mine(
        req, required_partners=[10, 11], viewer_id=42
    )


def test_pending_visible_for_user_mine_fully_confirmed_hidden():
    req = _Req(status="fully_confirmed", signatures=[_Sig(10)])
    assert not pending_confirmation_visible_for_user_mine(
        req, required_partners=[10], viewer_id=10
    )
