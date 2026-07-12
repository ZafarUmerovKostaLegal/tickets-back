from __future__ import annotations

import pytest

from application.labor_statistics_scope import (
    LaborStatisticsScope,
    clamp_labor_filter_param,
    resolve_labor_statistics_scope,
)


@pytest.mark.unit
def test_scope_all_with_permission():
    viewer = {"id": 1, "permissions": {"time_tracking_can_view_time_entries_scope": True}}
    assert resolve_labor_statistics_scope(viewer).mode == "all"


@pytest.mark.unit
def test_scope_partner_for_partner_org_role():
    viewer = {"id": 5, "role": "Партнер", "position": "Partner", "permissions": {}}
    scope = resolve_labor_statistics_scope(viewer)
    assert scope.mode == "partner"
    assert scope.auth_user_id == 5


@pytest.mark.unit
def test_clamp_partner_scope_keeps_partner_filter_optional():
    scope = LaborStatisticsScope(mode="partner", auth_user_id=7)
    partner, lawyer = clamp_labor_filter_param(scope, partner_id="99", lawyer_id=None)
    assert partner == "99"
    assert lawyer is None


@pytest.mark.unit
def test_clamp_lawyer_scope():
    scope = LaborStatisticsScope(mode="lawyer", auth_user_id=3)
    partner, lawyer = clamp_labor_filter_param(scope, partner_id=None, lawyer_id="9")
    assert lawyer == "3"
