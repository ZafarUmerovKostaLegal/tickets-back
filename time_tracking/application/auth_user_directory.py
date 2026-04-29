

from __future__ import annotations

import logging

import httpx

from infrastructure.config import get_settings

_log = logging.getLogger(__name__)


async def fetch_auth_user_positions_by_id(authorization: str) -> dict[int, str | None]:

    authz = (authorization or "").strip()
    if not authz:
        return {}
    base = (get_settings().auth_service_url or "").strip().rstrip("/")
    if not base:
        return {}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{base}/users",
                params={"include_archived": "true"},
                headers={"Authorization": authz},
            )
    except httpx.RequestError as e:
        _log.debug("auth users list for position merge: %s", e)
        return {}
    if r.status_code != 200:
        _log.debug("auth users list: HTTP %s", r.status_code)
        return {}
    data = r.json()
    if not isinstance(data, list):
        return {}
    out: dict[int, str | None] = {}
    for u in data:
        try:
            uid = u.get("id")
            if uid is None:
                continue
            out[int(uid)] = u.get("position")
        except (TypeError, ValueError):
            continue
    return out


async def fetch_auth_user_partner_hints_by_id(
    authorization: str,
) -> dict[int, dict[str, str | None]]:

    authz = (authorization or "").strip()
    if not authz:
        return {}
    base = (get_settings().auth_service_url or "").strip().rstrip("/")
    if not base:
        return {}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{base}/users",
                params={"include_archived": "true"},
                headers={"Authorization": authz},
            )
    except httpx.RequestError as e:
        _log.debug("auth users list for partner hints: %s", e)
        return {}
    if r.status_code != 200:
        _log.debug("auth users list for partner hints: HTTP %s", r.status_code)
        return {}
    data = r.json()
    if not isinstance(data, list):
        return {}
    out: dict[int, dict[str, str | None]] = {}
    for u in data:
        try:
            uid = u.get("id")
            if uid is None:
                continue
            i = int(uid)
        except (TypeError, ValueError):
            continue
        pos = u.get("position")
        pos_s = (str(pos).strip() if pos is not None and str(pos).strip() else None)
        role = u.get("role")
        role_s = (str(role).strip() if role is not None and str(role).strip() else None)
        out[i] = {"position": pos_s, "role": role_s}
    return out


async def fetch_auth_user_position(authorization: str, auth_user_id: int) -> str | None:

    authz = (authorization or "").strip()
    if not authz:
        return None
    base = (get_settings().auth_service_url or "").strip().rstrip("/")
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{base}/users/{auth_user_id}",
                headers={"Authorization": authz},
            )
    except httpx.RequestError as e:
        _log.debug("auth user %s for position: %s", auth_user_id, e)
        return None
    if r.status_code != 200:
        return None
    data = r.json()
    if not isinstance(data, dict):
        return None
    pos = data.get("position")
    if pos is None:
        return None
    s = str(pos).strip()
    return s if s else None
