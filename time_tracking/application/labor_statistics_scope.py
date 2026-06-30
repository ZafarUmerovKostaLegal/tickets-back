from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from application.project_partner_requirement import user_satisfies_partner_rule
from application.report_viewer_scope import viewer_sees_all_report_projects


LaborScopeMode = Literal["all", "partner", "lawyer"]


@dataclass(frozen=True)
class LaborStatisticsScope:
    mode: LaborScopeMode
    auth_user_id: int | None = None


def _viewer_permissions(viewer: dict) -> dict:
    perms = viewer.get("permissions")
    return perms if isinstance(perms, dict) else {}


def _viewer_id(viewer: dict) -> int:
    return int(viewer["id"])


def resolve_labor_statistics_scope(viewer: dict) -> LaborStatisticsScope:
    if _viewer_permissions(viewer).get("time_tracking_can_view_time_entries_scope"):
        return LaborStatisticsScope(mode="all")
    if viewer_sees_all_report_projects(viewer):
        return LaborStatisticsScope(mode="all")
    if user_satisfies_partner_rule(
        viewer.get("position"),
        viewer.get("position"),
        viewer.get("role"),
    ):
        return LaborStatisticsScope(mode="partner", auth_user_id=_viewer_id(viewer))
    return LaborStatisticsScope(mode="lawyer", auth_user_id=_viewer_id(viewer))


def clamp_labor_filter_param(
    scope: LaborStatisticsScope,
    *,
    partner_id: str | None,
    lawyer_id: str | None,
) -> tuple[str | None, str | None]:
    if scope.mode == "partner" and scope.auth_user_id is not None:
        forced = str(scope.auth_user_id)
        if partner_id and partner_id.strip() and partner_id.strip() != forced:
            return forced, lawyer_id
        return forced, lawyer_id
    if scope.mode == "lawyer" and scope.auth_user_id is not None:
        forced = str(scope.auth_user_id)
        if lawyer_id and lawyer_id.strip() and lawyer_id.strip() != forced:
            return partner_id, forced
        return partner_id, forced
    return partner_id, lawyer_id
