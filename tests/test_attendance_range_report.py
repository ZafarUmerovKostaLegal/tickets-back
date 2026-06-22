import pytest

from service_path import ensure_service_in_path

ensure_service_in_path("gateway")

from presentation.routes import attendance_routes


@pytest.mark.asyncio
async def test_attendance_range_report_returns_compact_linked_markers(monkeypatch):
    async def fake_daily(*, day: str | None = None, _: dict | None = None):
        return {
            "date": day,
            "items": [
                {
                    "app_user_id": 10,
                    "status": "late",
                    "first_event_time": f"{day}T09:17:00",
                    "camera_employee_no": "A10",
                    "display_name": "Late User",
                },
                {
                    "app_user_id": 11,
                    "status": "absent",
                    "first_event_time": None,
                    "camera_employee_no": "A11",
                    "display_name": "Absent User",
                    "explanation_text": "Called office",
                },
                {
                    "app_user_id": 12,
                    "status": "present_on_time",
                    "first_event_time": f"{day}T08:50:00",
                },
                {
                    "app_user_id": None,
                    "status": "late",
                    "camera_employee_no": "UNMAPPED",
                },
            ],
        }

    monkeypatch.setattr(attendance_routes, "get_daily_attendance_report", fake_daily)

    out = await attendance_routes.get_attendance_range_report(
        date_from="2026-01-01",
        date_to="2026-01-02",
        _={"role": "Главный администратор"},
    )

    assert out["date_from"] == "2026-01-01"
    assert out["date_to"] == "2026-01-02"
    assert len(out["items"]) == 4
    assert {x["status"] for x in out["items"]} == {"late", "absent"}
    assert {x["app_user_id"] for x in out["items"]} == {10, 11}


@pytest.mark.asyncio
async def test_attendance_range_report_rejects_regular_employee():
    with pytest.raises(attendance_routes.HTTPException) as exc:
        await attendance_routes.get_attendance_range_report(
            date_from="2026-01-01",
            date_to="2026-01-01",
            _={"role": "Сотрудник"},
        )

    assert exc.value.status_code == 403
