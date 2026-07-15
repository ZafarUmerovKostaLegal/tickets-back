from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from application.team_workload_builder import build_team_workload_members_and_summary


def test_build_team_workload_members_and_summary():
    users = [
        SimpleNamespace(
            auth_user_id=2,
            display_name="Bob",
            email="b@x",
            picture=None,
            weekly_capacity_hours=Decimal("35"),
        ),
        SimpleNamespace(
            auth_user_id=1,
            display_name="Ann",
            email="a@x",
            picture=None,
            weekly_capacity_hours=Decimal("40"),
        ),
    ]
    sums = {
        1: (Decimal("10"), Decimal("8"), Decimal("2")),
        2: (Decimal("5"), Decimal("5"), Decimal("0")),
    }
    members, summary = build_team_workload_members_and_summary(
        users,
        sums,
        date_from=date(2026, 7, 6),
        date_to=date(2026, 7, 12),
        entry_counts={1: 3, 2: 1},
    )
    assert [m.auth_user_id for m in members] == [1, 2]
    assert members[0].total_hours == Decimal("10")
    assert summary.total_hours == Decimal("15")
    assert summary.billable_hours == Decimal("13")
    assert float(summary.team_weekly_capacity_hours) == 75.0
