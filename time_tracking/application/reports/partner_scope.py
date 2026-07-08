
from __future__ import annotations

from fastapi import HTTPException

_MANAGE_ROLES_TIME_ENTRIES = frozenset({"Главный администратор", "Администратор", "Партнер"})


def _org_role(viewer: dict) -> str:
    return (viewer.get("role") or "").strip()


def normalize_partner_pending_scope(scope: str | None) -> str:
    normalized = (scope or "mine").strip().lower()
    if normalized in ("mine", ""):
        return "mine"
    if normalized == "all":
        return "all"
    raise HTTPException(status_code=400, detail="Invalid scope")


def _viewer_permissions(viewer: dict) -> dict:
    perms = viewer.get("permissions")
    return perms if isinstance(perms, dict) else {}


def viewer_can_view_all_pending_partner_confirmations(viewer: dict) -> bool:
    tt_role = (
        (viewer.get("time_tracking_role") or viewer.get("timeTrackingRole") or "")
    ).strip()
    if tt_role == "manager":
        return True
    if _org_role(viewer) in _MANAGE_ROLES_TIME_ENTRIES:
        return True
    if _viewer_permissions(viewer).get("time_tracking_can_manage_org_users") is True:
        return True
    return False


def pending_confirmation_visible_for_user_mine(
    request_row,
    *,
    required_partners: list[int],
    viewer_id: int,
    team_member_ids: set[int] | None = None,
    report_user_ids: set[int] | None = None,
) -> bool:
    if (getattr(request_row, "status", None) or "").strip() == "fully_confirmed":
        return False
    signatures = getattr(request_row, "signatures", None) or []
    signed_ids = {s.partner_auth_user_id for s in signatures}
    if viewer_id in signed_ids:
        return True
    if viewer_id not in required_partners:
        return False
    if team_member_ids is not None and report_user_ids is not None:
        from application.partner_confirmation_team_scope import partner_team_overlaps_report

        if not partner_team_overlaps_report(
            team_member_ids=team_member_ids,
            report_user_ids=report_user_ids,
        ):
            return False
    return True
