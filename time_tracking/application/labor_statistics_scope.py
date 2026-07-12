from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from application.access_control import _org_role
from application.project_partner_requirement import (
    org_role_indicates_partner,
    user_satisfies_partner_rule,
)
from application.report_viewer_scope import _REPORTS_UNSCOPED_ROLES, viewer_sees_all_report_projects


LaborScopeMode = Literal["all", "partner", "lawyer"]

# Org roles that see every team in labor statistics (Partner is excluded — own teams only).
_FULL_LABOR_STATS_ROLES = frozenset(_REPORTS_UNSCOPED_ROLES) | frozenset(
    {"Главный администратор", "Администратор"}
)


@dataclass(frozen=True)
class LaborStatisticsScope:
    mode: LaborScopeMode
    auth_user_id: int | None = None


def _viewer_permissions(viewer: dict) -> dict:
    perms = viewer.get("permissions")
    return perms if isinstance(perms, dict) else {}


def _viewer_id(viewer: dict) -> int:
    return int(viewer["id"])


def viewer_has_full_labor_statistics_access(viewer: dict) -> bool:
    """Admins / explicit scope permission — all teams. Partners do not qualify."""
    if _viewer_permissions(viewer).get("time_tracking_can_view_time_entries_scope"):
        return True
    return _org_role(viewer) in _FULL_LABOR_STATS_ROLES


def viewer_is_team_leader_scope(viewer: dict) -> bool:
    """Partner (org/position) without full admin access → limit to teams they lead."""
    if viewer_has_full_labor_statistics_access(viewer):
        return False
    if org_role_indicates_partner(_org_role(viewer)):
        return True
    return user_satisfies_partner_rule(
        viewer.get("position"),
        viewer.get("position"),
        viewer.get("role"),
    )


def resolve_labor_statistics_scope(viewer: dict) -> LaborStatisticsScope:
    if viewer_has_full_labor_statistics_access(viewer):
        return LaborStatisticsScope(mode="all")
    # Org-role Partner previously fell into "all" via manage roles; for statistics
    # they are team leaders and only see members of teams they partner.
    if viewer_is_team_leader_scope(viewer):
        return LaborStatisticsScope(mode="partner", auth_user_id=_viewer_id(viewer))
    if viewer_sees_all_report_projects(viewer):
        # Remaining manage roles that are not full-stats admins (should be rare).
        return LaborStatisticsScope(mode="all")
    return LaborStatisticsScope(mode="lawyer", auth_user_id=_viewer_id(viewer))


def clamp_labor_filter_param(
    scope: LaborStatisticsScope,
    *,
    partner_id: str | None,
    lawyer_id: str | None,
) -> tuple[str | None, str | None]:
    # Partner/team-leader scope is enforced via team membership, not project-partner id.
    if scope.mode == "partner":
        return partner_id, lawyer_id
    if scope.mode == "lawyer" and scope.auth_user_id is not None:
        forced = str(scope.auth_user_id)
        if lawyer_id and lawyer_id.strip() and lawyer_id.strip() != forced:
            return partner_id, forced
        return partner_id, forced
    return partner_id, lawyer_id
