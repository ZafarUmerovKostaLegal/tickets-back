from __future__ import annotations

from typing import Any

from backend_common.positions import normalize_position

                                                                                    
                                           
TT_FULL_OPS_NO_REPORTS_POSITIONS: frozenset[str] = frozenset(
    {
        "business development manager",
        "contracts and bd assistant",
        "accountant",
    }
)


def position_key(position: str | None) -> str:
    return (normalize_position(position) or "").casefold()


def position_has_tt_full_ops_no_reports(position: str | None) -> bool:
    return position_key(position) in TT_FULL_OPS_NO_REPORTS_POSITIONS


def user_has_tt_full_ops_no_reports(user: dict[str, Any] | None) -> bool:
    if not user:
        return False
    return position_has_tt_full_ops_no_reports(user.get("position"))
