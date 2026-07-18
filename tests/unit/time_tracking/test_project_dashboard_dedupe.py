"""Project dashboard must collapse duplicates the same way as the time report."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from support.service_path import ensure_service_in_path


def test_dedupe_dashboard_entries_collapses_near_duplicates():
    ensure_service_in_path("time_tracking")
    from application.project_dashboard import _dedupe_dashboard_entries

    def entry(eid: str, hours: str):
        return SimpleNamespace(
            id=eid,
            auth_user_id=7,
            work_date=date(2026, 6, 10),
            task_id="task-1",
            description="same note",
            hours=Decimal(hours),
            rounded_hours=Decimal(hours),
            is_billable=True,
            project_id="proj-1",
            created_at=datetime(2026, 6, 10, 9, 0, tzinfo=timezone.utc),
            duration_seconds=int(Decimal(hours) * 3600),
        )

    proj = SimpleNamespace(id="proj-1", currency="EUR", project_type="hourly")
    a = entry("a", "2.5")
    b = entry("b", "2.5")
    kept = _dedupe_dashboard_entries(
        [a, b],
        proj_row=proj,
        rates_map={},
        tasks_map={},
        package_splits=None,
    )
    assert len(kept) == 1
    assert kept[0].id in ("a", "b")
