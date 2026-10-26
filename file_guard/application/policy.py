from __future__ import annotations

from infrastructure.models import FileGuardPolicyModel


def pick_daily_send_limit(
    rows: list[FileGuardPolicyModel],
    *,
    host: str,
    windows_user: str,
) -> int | None:
    """Most specific scope wins. None means the limit is not set."""
    host_key = (host or "").strip().lower()
    user_key = (windows_user or "").strip().lower()
    ranked: list[tuple[int, int | None]] = []
    for row in rows:
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
