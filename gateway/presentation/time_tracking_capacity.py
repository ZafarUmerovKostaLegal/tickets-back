

from typing import Any, Optional

import httpx

from infrastructure.config import get_settings


def _time_tracking_base() -> str | None:
    b = (get_settings().time_tracking_service_url or "").strip()
    return b.rstrip("/") if b else None


async def fetch_weekly_capacity_hours(
    auth_user_id: int,
    authorization: Optional[str] = None,
) -> Optional[float]:

    base = _time_tracking_base()
    if not base:
        return None
    headers = {"Authorization": authorization} if authorization else None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{base}/users/{auth_user_id}", headers=headers)
    except httpx.RequestError:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("weekly_capacity_hours")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


async def merge_weekly_capacity_into_user(
    user: dict[str, Any],
    authorization: Optional[str] = None,
) -> dict[str, Any]:
    uid = user.get("id")
    if uid is not None:
        user["weekly_capacity_hours"] = await fetch_weekly_capacity_hours(int(uid), authorization)
    else:
        user["weekly_capacity_hours"] = None
    return user
