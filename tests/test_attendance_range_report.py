from datetime import date

import pytest

from service_path import ensure_service_in_path

ensure_service_in_path("gateway")

from presentation.routes import attendance_routes


def test_build_range_report_items_returns_only_linked_late_and_absent():
    roster = {
        "A10": {"camera_employee_no": "A10", "camera_name": "Late User", "department": None, "camera_ips": set()},
        "A11": {"camera_employee_no": "A11", "camera_name": "Absent User", "department": None, "camera_ips": set()},
        "A12": {"camera_employee_no": "A12", "camera_name": "On Time", "department": None, "camera_ips": set()},
        "UNMAPPED": {"camera_employee_no": "UNMAPPED", "camera_name": "No Link", "department": None, "camera_ips": set()},
    }
    mappings = {
        "A10": {"app_user_id": 10},
        "A11": {"app_user_id": 11},
        "A12": {"app_user_id": 12},
    }
    app_users = {
        10: {"display_name": "Late User", "email": "late@example.com"},
        11: {"display_name": "Absent User", "email": "absent@example.com"},
        12: {"display_name": "On Time", "email": "ontime@example.com"},
    }
    first_events = {
        "2026-01-01": {
            "A10": {"dt": attendance_routes.datetime.fromisoformat("2026-01-01T09:17:00"), "record": {}},
            "A12": {"dt": attendance_routes.datetime.fromisoformat("2026-01-01T08:50:00"), "record": {}},
        },
        "2026-01-02": {
            "A10": {"dt": attendance_routes.datetime.fromisoformat("2026-01-02T09:17:00"), "record": {}},
            "A12": {"dt": attendance_routes.datetime.fromisoformat("2026-01-02T08:50:00"), "record": {}},
        },
    }
    explanations = [
        {
            "day": "2026-01-01",
            "camera_employee_no": "A11",
            "status": "absent",
            "explanation_text": "Called office",
        }
    ]

    items = attendance_routes._build_range_report_items(
        start=date(2026, 1, 1),
        end=date(2026, 1, 2),
        workday={"workday_start": "09:00:00", "late_threshold_minutes": 0},
        roster_by_employee_no=roster,
        mapping_by_employee_no=mappings,
        app_users_by_id=app_users,
        first_events_by_day=first_events,
        explanations=explanations,
    )

    assert len(items) == 4
    assert {x["status"] for x in items} == {"late", "absent"}
    assert {x["app_user_id"] for x in items} == {10, 11}
    absent = next(x for x in items if x["app_user_id"] == 11 and x["date"] == "2026-01-01")
    assert absent["status"] == "absent"
    assert absent["explanation_text"] == "Called office"


def test_index_first_events_by_day_keeps_earliest_event():
    events_devices = [
        {
            "camera_ip": "10.0.0.1",
            "records": [
                {"person_id": "A10", "time": "2026-01-01T10:00:00"},
                {"person_id": "A10", "time": "2026-01-01T09:05:00"},
                {"person_id": "A10", "time": "2026-01-02T09:05:00"},
            ],
        }
    ]

    indexed = attendance_routes._index_first_events_by_day(
        events_devices,
        start=date(2026, 1, 1),
        end=date(2026, 1, 2),
    )

    assert indexed["2026-01-01"]["A10"]["dt"] == attendance_routes.datetime.fromisoformat("2026-01-01T09:05:00")
    assert indexed["2026-01-02"]["A10"]["dt"] == attendance_routes.datetime.fromisoformat("2026-01-02T09:05:00")


@pytest.mark.asyncio
async def test_attendance_range_report_rejects_regular_employee():
    with pytest.raises(attendance_routes.HTTPException) as exc:
        await attendance_routes.get_attendance_range_report(
            date_from="2026-01-01",
            date_to="2026-01-01",
            _={"role": "Сотрудник"},
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_attendance_range_report_uses_bulk_fetch(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    captured_explanation_params: list[dict] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None, headers=None):
            if url.endswith("/settings/workday"):
                return FakeResponse({"workday_start": "09:00:00", "late_threshold_minutes": 0})
            if url.endswith("/hikvision/mappings"):
                return FakeResponse([{"camera_employee_no": "A10", "app_user_id": 10}])
            if url.endswith("/hikvision/explanations"):
                captured_explanation_params.append(dict(params or {}))
                return FakeResponse([])
            if url.endswith("/users"):
                return FakeResponse([{"id": 10, "display_name": "Late User", "email": "late@example.com"}])
            raise AssertionError(f"unexpected url: {url}")

    async def fake_fetch_events(client, base, allowed, start, end, **kwargs):
        return [
            {
                "camera_ip": "10.0.0.1",
                "records": [{"person_id": "A10", "time": "2026-01-01T09:17:00"}],
            }
        ]

    async def fake_fetch_users(client, base, params):
        return [
            {
                "camera_ip": "10.0.0.1",
                "users": [{"employee_no": "A10", "name": "Late User"}],
            }
        ]

    attendance_routes._hikvision_users_cache.update({"expires_at": 0.0, "key": "", "payload": None})
    monkeypatch.setattr(attendance_routes.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(attendance_routes, "_fetch_attendance_events_for_range", fake_fetch_events)
    monkeypatch.setattr(attendance_routes, "_fetch_hikvision_users_devices", fake_fetch_users)

    from infrastructure.config import Settings

    fake_settings = Settings(
        ATTENDANCE_SERVICE_URL="http://attendance:1250",
        AUTH_SERVICE_URL="http://auth:1236",
        ATTENDANCE_HIKVISION_ALLOWED_IPS="",
    )
    monkeypatch.setattr(attendance_routes, "get_settings", lambda: fake_settings)

    out = await attendance_routes.get_attendance_range_report(
        date_from="2026-01-01",
        date_to="2026-01-01",
        _={"role": "Главный администратор"},
    )

    assert out["date_from"] == "2026-01-01"
    assert out["date_to"] == "2026-01-01"
    assert len(out["items"]) == 1
    assert out["items"][0]["app_user_id"] == 10
    assert out["items"][0]["status"] == "late"
    assert captured_explanation_params == [{"date_from": "2026-01-01", "date_to": "2026-01-01"}]
