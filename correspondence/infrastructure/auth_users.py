

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

_log = logging.getLogger(__name__)
_MAX_CONCURRENT = 12


async def fetch_user_by_id(
    auth_base_url: str,
    authorization: str | None,
    user_id: int,
) -> dict | None:
    if not authorization or not authorization.strip():
        return None
    hdr = authorization.strip()
    if not hdr.lower().startswith("bearer "):
        hdr = f"Bearer {hdr}"
    base = auth_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(f"{base}/users/{user_id}", headers={"Authorization": hdr})
        if r.status_code == 200:
            return r.json()
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
            except httpx.RequestError as e:
                _log.debug("auth users/%s err=%s", uid, e)
            return uid, None

    pairs = await asyncio.gather(*(one(uid) for uid in user_ids))
    return {uid: data for uid, data in pairs if data is not None}
