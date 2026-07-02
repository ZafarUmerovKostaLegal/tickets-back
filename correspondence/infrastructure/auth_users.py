
from __future__ import annotations

import logging
from typing import Optional

import httpx

_log = logging.getLogger(__name__)


def _auth_headers(authorization: str | None) -> dict[str, str] | None:
    if not authorization or not authorization.strip():
        return None
    hdr = authorization.strip()
    if not hdr.lower().startswith("bearer "):
        hdr = f"Bearer {hdr}"
    return {"Authorization": hdr}


def _public_user_to_profile(data: dict) -> dict:
    return {
        "id": data.get("id"),
        "display_name": data.get("display_name") or data.get("displayName"),
        "email": data.get("email"),
        "picture": data.get("picture"),
        "position": data.get("position"),
        "role": data.get("role"),
    }


async def fetch_user_by_id(
    auth_base_url: str,
    authorization: str | None,
    user_id: int,
) -> dict | None:
    headers = _auth_headers(authorization)
    if headers is None:
        return None
    base = auth_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(f"{base}/users/{user_id}/public", headers=headers)
        if r.status_code == 200:
            return _public_user_to_profile(r.json())
    except httpx.RequestError as e:
        _log.debug("auth users/%s/public err=%s", user_id, e)
    return None


async def fetch_users_by_ids(
    auth_base_url: str,
    authorization: Optional[str],
    user_ids: set[int],
) -> dict[int, dict]:
    headers = _auth_headers(authorization)
    if headers is None or not user_ids:
        return {}
    base = auth_base_url.rstrip("/")
    ids_param = ",".join(str(uid) for uid in sorted(user_ids))
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get(
                f"{base}/users/public",
                params={"ids": ids_param, "include_archived": "true"},
                headers=headers,
            )
        if r.status_code != 200:
            return {}
        payload = r.json()
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return {}
        out: dict[int, dict] = {}
        for raw in items:
            if not isinstance(raw, dict):
                continue
            uid = raw.get("id")
            if uid is None:
                continue
            try:
                out[int(uid)] = _public_user_to_profile(raw)
            except (TypeError, ValueError):
                continue
        return out
    except httpx.RequestError as e:
        _log.debug("auth users/public err=%s", e)
        return {}
