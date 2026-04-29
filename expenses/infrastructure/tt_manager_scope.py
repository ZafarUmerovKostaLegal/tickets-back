

from __future__ import annotations

import httpx

from infrastructure.config import get_settings


async def fetch_managed_scope_user_ids(manager_auth_user_id: int) -> set[int]:

    base = (get_settings().time_tracking_service_url or "").strip().rstrip("/")
    if not base:
        return {int(manager_auth_user_id)}
    url = f"{base}/users/managed-scope/{int(manager_auth_user_id)}"
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get(url)
    except httpx.HTTPError:
        return {int(manager_auth_user_id)}
    if r.status_code >= 400:
        return {int(manager_auth_user_id)}
    try:
        data = r.json()
    except (TypeError, ValueError):
        return {int(manager_auth_user_id)}
    if not isinstance(data, list):
        return {int(manager_auth_user_id)}
    out: set[int] = set()
    for u in data:
        if not isinstance(u, dict):
            continue
        try:
            out.add(int(u.get("id")))
        except (TypeError, ValueError):
            continue
    if not out:
        out.add(int(manager_auth_user_id))
    return out
