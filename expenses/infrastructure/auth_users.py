

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

_log = logging.getLogger(__name__)
_MAX_CONCURRENT = 12


def _normalize_authorization_header(raw: str | None) -> str | None:
    a = (raw or "").strip()
    if not a:
        return None
    if a.lower().startswith("bearer "):
        return a
    return f"Bearer {a}"


async def fetch_user_by_id(
    auth_base_url: str,
    authorization: str | None,
    user_id: int,
    *,
    fallback_bearer: str | None = None,
) -> dict | None:

    hdr = _normalize_authorization_header(authorization)
    if not hdr:
        hdr = _normalize_authorization_header(fallback_bearer)
    if not hdr:
        return None
    base = auth_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                f"{base}/users/{user_id}",
                headers={"Authorization": hdr},
            )
        if r.status_code == 200:
            return r.json()
        _log.debug("auth users/%s status=%s", user_id, r.status_code)
    except httpx.RequestError as e:
        _log.debug("auth users/%s err=%s", user_id, e)
    return None


async def fetch_users_by_ids(
    auth_base_url: str,
    authorization: Optional[str],
    user_ids: set[int],
) -> dict[int, dict]:

    if not authorization or not user_ids:
        return {}
    base = auth_base_url.rstrip("/")
    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def one(uid: int) -> tuple[int, dict | None]:
        async with sem:
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    r = await client.get(
                        f"{base}/users/{uid}",
                        headers={"Authorization": authorization},
                    )
                if r.status_code == 200:
                    return uid, r.json()
                _log.debug("auth users/%s status=%s", uid, r.status_code)
            except httpx.RequestError as e:
                _log.debug("auth users/%s err=%s", uid, e)
            return uid, None

    pairs = await asyncio.gather(*(one(uid) for uid in user_ids))
    out: dict[int, dict] = {}
    for uid, data in pairs:
        if data is not None:
            out[uid] = data
    return out
