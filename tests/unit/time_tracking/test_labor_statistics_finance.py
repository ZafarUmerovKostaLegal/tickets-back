"""Unit tests for labor statistics KPI / team finance / partner scope."""

from __future__ import annotations

import pytest

from support.service_path import ensure_service_in_path


@pytest.mark.unit
def test_partner_org_role_is_team_leader_scope_not_all():
    ensure_service_in_path("time_tracking")
    from application.labor_statistics_scope import resolve_labor_statistics_scope

    viewer = {"id": 5, "role": "Партнер", "position": "Partner", "permissions": {}}
    scope = resolve_labor_statistics_scope(viewer)
    assert scope.mode == "partner"
    assert scope.auth_user_id == 5


@pytest.mark.unit
def test_admin_keeps_full_labor_statistics_access():
    ensure_service_in_path("time_tracking")
    from application.labor_statistics_scope import (
        resolve_labor_statistics_scope,
        viewer_has_full_labor_statistics_access,
    )

    viewer = {"id": 1, "role": "Администратор", "permissions": {}}
    assert viewer_has_full_labor_statistics_access(viewer) is True
    assert resolve_labor_statistics_scope(viewer).mode == "all"


@pytest.mark.unit
def test_clamp_partner_does_not_force_project_partner_filter():
    ensure_service_in_path("time_tracking")
    from application.labor_statistics_scope import LaborStatisticsScope, clamp_labor_filter_param

    scope = LaborStatisticsScope(mode="partner", auth_user_id=7)
    partner, lawyer = clamp_labor_filter_param(scope, partner_id="99", lawyer_id=None)
    assert partner == "99"
    assert lawyer is None


@pytest.mark.unit
def test_build_kpi_includes_accrued_and_paid():
    ensure_service_in_path("time_tracking")
    from application.labor_statistics_service import _build_kpi

    rows = [
        {
            "hours": 10,
            "billable_hours": 8,
            "billable_amount": 800,
            "payment": 500,
            "currency": "USD",
        },
        {
            "hours": 5,
            "billable_hours": 5,
            "billable_amount": 400,
            "payment": 0,
            "currency": "USD",
        },
    ]
    kpi = _build_kpi(rows)
    assert kpi["total_hours"] == 15
    assert kpi["billable_hours"] == 13
    assert kpi["billable_amount"] == 1200
    assert kpi["paid_amount"] == 500
    assert kpi["accrued_rate_per_hour"] == round(1200 / 13, 2)
    assert kpi["rate_per_hour"] == round(500 / 13, 2)


@pytest.mark.unit
def test_by_teams_finance_aggregates_amounts():
    ensure_service_in_path("time_tracking")
    from application.labor_statistics_service import _build_charts

    rows = [
        {
            "team_id": "t1",
            "team_name": "Alpha",
            "lawyer_id": "1",
            "lawyer_name": "A",
            "project_id": "p1",
            "project_name": "P1",
            "client_id": "c1",
            "client_name": "C1",
            "project_status_id": "active",
            "project_status": "Active",
            "hours": 10,
            "billable_hours": 10,
            "billable_amount": 1000,
            "payment": 400,
            "currency": "USD",
            "work_type": "Legal",
        },
        {
            "team_id": "t1",
            "team_name": "Alpha",
            "lawyer_id": "2",
            "lawyer_name": "B",
            "project_id": "p2",
            "project_name": "P2",
            "client_id": "c1",
            "client_name": "C1",
            "project_status_id": "active",
            "project_status": "Active",
            "hours": 5,
            "billable_hours": 4,
            "billable_amount": 200,
            "payment": 100,
            "currency": "USD",
            "work_type": "Legal",
        },
        {
            "team_id": "t2",
            "team_name": "Beta",
            "lawyer_id": "3",
            "lawyer_name": "C",
            "project_id": "p3",
            "project_name": "P3",
            "client_id": "c2",
            "client_name": "C2",
            "project_status_id": "active",
            "project_status": "Active",
            "hours": 3,
            "billable_hours": 3,
            "billable_amount": 150,
            "payment": 50,
            "currency": "USD",
            "work_type": "Admin",
        },
    ]
    charts = _build_charts(rows, [])
    assert "by_teams" in charts
    assert len(charts["by_teams"]) >= 2
    finance = {r["team_id"]: r for r in charts["by_teams_finance"]}
    assert finance["t1"]["hours"] == 15
    assert finance["t1"]["billable_hours"] == 14
    assert finance["t1"]["billable_amount"] == 1200
    assert finance["t1"]["paid_amount"] == 500
    assert finance["t2"]["billable_amount"] == 150
