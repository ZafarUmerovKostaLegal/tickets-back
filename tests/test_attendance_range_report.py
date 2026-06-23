from datetime import date, datetime, timezone

import pytest

from service_path import ensure_service_in_path

ensure_service_in_path("gateway")

from application import attendance_range_builder as builder
from infrastructure import attendance_range_snapshot as snapshot


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
            "A10": {"dt": datetime.fromisoformat("2026-01-01T09:17:00"), "record": {}},
            "A12": {"dt": datetime.fromisoformat("2026-01-01T08:50:00"), "record": {}},
        },
        "2026-01-02": {
            "A10": {"dt": datetime.fromisoformat("2026-01-02T09:17:00"), "record": {}},
            "A12": {"dt": datetime.fromisoformat("2026-01-02T08:50:00"), "record": {}},
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

    items = builder.build_range_report_items(
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

    indexed = builder.index_first_events_by_day(
        events_devices,
        start=date(2026, 1, 1),
        end=date(2026, 1, 2),
    )

    assert indexed["2026-01-01"]["A10"]["dt"] == datetime.fromisoformat("2026-01-01T09:05:00")
    assert indexed["2026-01-02"]["A10"]["dt"] == datetime.fromisoformat("2026-01-02T09:05:00")


def test_build_range_roster_from_mappings():
    roster = builder.build_range_roster_from_mappings(
        [
            {"camera_employee_no": "A10", "camera_name": "Ivan", "app_user_id": 10},
            {"camera_employee_no": "  ", "camera_name": "Skip"},
        ]
    )
    assert list(roster.keys()) == ["A10"]
    assert roster["A10"]["camera_name"] == "Ivan"


def test_build_hikvision_roster_matches_daily_shape():
    roster = builder.build_hikvision_roster(
        [
            {
                "camera_ip": "10.0.0.1",
                "users": [
                    {"employee_no": "A10", "name": "Ivan", "department": "Legal"},
                    {"employee_no": "A10", "name": "Ivan", "department": "Legal"},
                ],
            },
            {
                "camera_ip": "10.0.0.2",
                "users": [{"employee_no": "A11", "name": "Petr"}],
            },
        ]
    )
    assert set(roster.keys()) == {"A10", "A11"}
    assert roster["A10"]["camera_name"] == "Ivan"
    assert roster["A10"]["department"] == "Legal"
    assert roster["A10"]["camera_ips"] == {"10.0.0.1"}


def test_daily_and_range_markers_match_for_same_day():
    """Маркеры range (late/absent) должны совпадать с report/daily для привязанных сотрудников."""
    report_day = date(2026, 1, 1)
    roster = {
        "A10": {"camera_employee_no": "A10", "camera_name": "Late User", "department": None, "camera_ips": {"10.0.0.1"}},
        "A11": {"camera_employee_no": "A11", "camera_name": "Absent User", "department": None, "camera_ips": set()},
        "A12": {"camera_employee_no": "A12", "camera_name": "On Time", "department": None, "camera_ips": set()},
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
    first_events_for_day = {
        "A10": {"dt": datetime.fromisoformat("2026-01-01T09:17:00"), "record": {}},
        "A12": {"dt": datetime.fromisoformat("2026-01-01T08:50:00"), "record": {}},
    }
    first_events_by_day = {"2026-01-01": first_events_for_day}
    workday = {"workday_start": "09:00:00", "late_threshold_minutes": 0}

    daily_items, _, _ = builder.build_daily_report_items(
        report_day,
        workday=workday,
        roster_by_employee_no=roster,
        mapping_by_employee_no=mappings,
        app_users_by_id=app_users,
        first_events_for_day=first_events_for_day,
        explanations_for_day=[],
    )
    daily_markers = {
        (x["app_user_id"], x["status"], x["first_event_time"])
        for x in daily_items
        if x.get("app_user_id") is not None and x["status"] in {"late", "absent"}
    }

    range_items = builder.build_range_report_items(
        start=report_day,
        end=report_day,
        workday=workday,
        roster_by_employee_no=roster,
        mapping_by_employee_no=mappings,
        app_users_by_id=app_users,
        first_events_by_day=first_events_by_day,
        explanations=[],
    )
    range_markers = {
        (x["app_user_id"], x["status"], x["first_event_time"]) for x in range_items
    }

    assert daily_markers == range_markers
    assert daily_markers == {(10, "late", "2026-01-01T09:17:00"), (11, "absent", None)}


def test_month_chunks_inclusive():
    chunks = builder.month_chunks_inclusive(date(2026, 1, 15), date(2026, 3, 10))
    assert chunks == [
        (date(2026, 1, 15), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 10)),
    ]


def test_snapshot_filter_by_month():
    today = date.today()
    year = today.year
    state = snapshot.get_snapshot_state()
    state.items = [
        {"date": f"{year}-01-15", "app_user_id": 1, "status": "late"},
        {"date": f"{year}-02-03", "app_user_id": 2, "status": "absent"},
    ]
    state.year = year
    state.coverage_start = date(year, 1, 1)
    state.coverage_end = date(year, 2, 28)
    state.built_at = datetime.now(timezone.utc)
    state.building = False

    resp = snapshot.try_get_snapshot_response(date(year, 1, 1), date(year, 1, 31))
    assert resp is not None
    assert len(resp["items"]) == 1
    assert resp["items"][0]["app_user_id"] == 1
    assert resp["snapshot"]["status"] == "ready"


@pytest.mark.asyncio
async def test_attendance_range_report_rejects_regular_employee():
    from presentation.routes import attendance_routes

    with pytest.raises(attendance_routes.HTTPException) as exc:
        await attendance_routes.get_attendance_range_report(
            date_from="2026-01-01",
            date_to="2026-01-01",
            _={"role": "Сотрудник"},
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_attendance_range_report_serves_from_snapshot(monkeypatch):
    from presentation.routes import attendance_routes

    today = date.today()
    year = today.year
    day = f"{year}-01-01"
    state = snapshot.get_snapshot_state()
    state.items = [
        {
            "date": day,
            "app_user_id": 10,
            "status": "late",
            "first_event_time": f"{day}T09:17:00",
            "camera_employee_no": "A10",
            "display_name": "Late User",
        }
    ]
    state.year = year
    state.coverage_start = date(year, 1, 1)
    state.coverage_end = date(year, 12, 31)
    state.built_at = datetime.now(timezone.utc)
    state.building = False

    async def fail_live_fetch(*args, **kwargs):
        raise AssertionError("live fetch should not run when snapshot is ready")

    monkeypatch.setattr(attendance_routes, "fetch_attendance_range_items", fail_live_fetch)

    out = await attendance_routes.get_attendance_range_report(
        date_from=day,
        date_to=day,
        _={"role": "Главный администратор"},
    )

    assert len(out["items"]) == 1
    assert out["snapshot"]["status"] == "ready"
