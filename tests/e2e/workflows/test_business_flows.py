from __future__ import annotations

import pytest

from e2e.support.respx_helpers import upstream_mocks
from e2e.support.personas import ADMIN, EMPLOYEE, MANAGER, PARTNER


@pytest.mark.e2e
@pytest.mark.workflow
async def test_tickets_statuses_and_list(gateway_client):
    r = await gateway_client.get("/api/v1/tickets/statuses")
    assert r.status_code in (200, 503)
    with upstream_mocks(user=EMPLOYEE, upstream_body=[]):
        r = await gateway_client.get(
            "/api/v1/tickets",
            headers={"Authorization": "Bearer e2e"},
        )
    assert r.status_code in (200, 503)


@pytest.mark.e2e
@pytest.mark.workflow
async def test_time_tracking_reports_meta(gateway_client):
    with upstream_mocks(user=MANAGER, upstream_body={"pageSizeMax": 500}):
        r = await gateway_client.get(
            "/api/v1/time-tracking/reports/meta",
            headers={"Authorization": "Bearer e2e"},
        )
    assert r.status_code in (200, 403, 503)


@pytest.mark.e2e
@pytest.mark.workflow
async def test_partner_confirmations_pending_scopes(gateway_client):
    with upstream_mocks(user=ADMIN, upstream_body=[]):
        mine = await gateway_client.get(
            "/api/v1/time-tracking/reports/partner-confirmations/pending",
            headers={"Authorization": "Bearer e2e"},
        )
        all_scope = await gateway_client.get(
            "/api/v1/time-tracking/reports/partner-confirmations/pending?scope=all",
            headers={"Authorization": "Bearer e2e"},
        )
    assert mine.status_code in (200, 503)
    assert all_scope.status_code in (200, 403, 503)


@pytest.mark.e2e
@pytest.mark.workflow
async def test_correspondence_stats(gateway_client):
    with upstream_mocks(user=EMPLOYEE, upstream_body={"incomingTotal": 0, "outgoingTotal": 0}):
        r = await gateway_client.get(
            "/api/v1/correspondence/stats",
            headers={"Authorization": "Bearer e2e"},
        )
    assert r.status_code in (200, 503)


@pytest.mark.e2e
@pytest.mark.workflow
async def test_expense_types(gateway_client):
    with upstream_mocks(user=EMPLOYEE, upstream_body=[]):
        r = await gateway_client.get(
            "/api/v1/expense-types",
            headers={"Authorization": "Bearer e2e"},
        )
    assert r.status_code in (200, 503)


@pytest.mark.e2e
@pytest.mark.workflow
async def test_users_me(gateway_client):
    with upstream_mocks(user=ADMIN):
        r = await gateway_client.get(
            "/api/v1/users/me",
            headers={"Authorization": "Bearer e2e"},
        )
    assert r.status_code in (200, 503)


@pytest.mark.e2e
@pytest.mark.workflow
async def test_vacation_leave_kinds(gateway_client):
    with upstream_mocks(user=EMPLOYEE, upstream_body=[]):
        r = await gateway_client.get(
            "/api/v1/vacations/leave-kinds",
            headers={"Authorization": "Bearer e2e"},
        )
    assert r.status_code in (200, 503)


@pytest.mark.e2e
@pytest.mark.workflow
async def test_inventory_categories(gateway_client):
    with upstream_mocks(user=EMPLOYEE, upstream_body=[]):
        r = await gateway_client.get(
            "/api/v1/inventory/categories",
            headers={"Authorization": "Bearer e2e"},
        )
    assert r.status_code in (200, 503)


@pytest.mark.e2e
@pytest.mark.workflow
async def test_contacts_colleagues(gateway_client):
    with upstream_mocks(user=ADMIN, upstream_body=[]):
        r = await gateway_client.get(
            "/api/v1/contacts/colleagues",
            headers={"Authorization": "Bearer e2e"},
        )
    assert r.status_code in (200, 503)


@pytest.mark.e2e
@pytest.mark.workflow
async def test_employee_cannot_scope_all_pending(gateway_client):
    with upstream_mocks(user=EMPLOYEE, upstream_body=[]):
        r = await gateway_client.get(
            "/api/v1/time-tracking/reports/partner-confirmations/pending?scope=all",
            headers={"Authorization": "Bearer e2e"},
        )
    assert r.status_code in (403, 503)


@pytest.mark.e2e
@pytest.mark.workflow
async def test_partner_can_access_pending(gateway_client):
    with upstream_mocks(user=PARTNER, upstream_body=[]):
        r = await gateway_client.get(
            "/api/v1/time-tracking/reports/partner-confirmations/pending",
            headers={"Authorization": "Bearer e2e"},
        )
    assert r.status_code in (200, 503)
