from __future__ import annotations

from infrastructure.models import FileGuardPolicyModel


def pick_daily_send_limit(
    rows: list[FileGuardPolicyModel],
    *,
    host: str,
    windows_user: str,
) -> int | None:
    """Most specific scope with a number wins. The number is megabytes per day.

    A row without a number does not override a broader limit. None means no
    limit is configured, and the agent then blocks every send.
    """
    host_key = (host or "").strip().lower()
    user_key = (windows_user or "").strip().lower()
    ranked: list[tuple[int, int]] = []
    for row in rows:
        if row.daily_send_limit is None:
            continue
        row_host = (row.host or "*").strip().lower()
        row_user = (row.windows_user or "*").strip().lower()
        if row_host not in {host_key, "*"} or row_user not in {user_key, "*"}:
            continue
        score = (0 if row_host == "*" else 2) + (0 if row_user == "*" else 1)
        ranked.append((score, row.daily_send_limit))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]
