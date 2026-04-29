

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal

from application.team_workload_math import capacity_for_period, workload_percent
from presentation.schemas import TeamWorkloadMemberOut, TeamWorkloadSummaryOut


def build_team_workload_members_and_summary(
    users: Iterable,
    sums: Mapping[int, tuple[Decimal, Decimal, Decimal]],
    *,
    date_from: date,
    date_to: date,
) -> tuple[list[TeamWorkloadMemberOut], TeamWorkloadSummaryOut]:
    members: list[TeamWorkloadMemberOut] = []
    total_hours = Decimal("0")
    billable_sum = Decimal("0")
    non_sum = Decimal("0")
    team_cap = Decimal("0")
    team_weekly = Decimal("0")

    for user in sorted(users, key=lambda row: (row.display_name or row.email or "").lower()):
        cap = capacity_for_period(user.weekly_capacity_hours, date_from, date_to)
        tot, bill, nonb = sums.get(user.auth_user_id, (Decimal("0"), Decimal("0"), Decimal("0")))
        total_hours += tot
        billable_sum += bill
        non_sum += nonb
        team_cap += cap
        team_weekly += user.weekly_capacity_hours
        members.append(
            TeamWorkloadMemberOut(
                auth_user_id=user.auth_user_id,
                display_name=user.display_name,
                email=user.email,
                picture=user.picture,
                capacity_hours=cap,
                total_hours=tot,
                billable_hours=bill,
                non_billable_hours=nonb,
                workload_percent=workload_percent(tot, cap),
            )
        )

    summary = TeamWorkloadSummaryOut(
        total_hours=total_hours,
        team_capacity_hours=team_cap,
        team_weekly_capacity_hours=team_weekly,
        billable_hours=billable_sum,
        non_billable_hours=non_sum,
        team_workload_percent=workload_percent(total_hours, team_cap),
    )
    return members, summary
