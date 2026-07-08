
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from infrastructure.config import get_settings
from infrastructure.database import make_async_url

_log = logging.getLogger(__name__)


def initials_from_display_name(name: str | None, email: str | None = None) -> str:
    src = (name or email or "").strip()
    if not src:
        return "?"
    if "@" in src and not (name or "").strip():
        src = src.split("@", 1)[0]
    parts = src.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return src[0].upper()


def resolve_user_initials(
    user: Any | None,
    *,
    initials_map: dict[int, str | None] | None = None,
) -> str | None:
    if user is None:
        return None
    uid = getattr(user, "auth_user_id", None)
    custom: str | None = None
    stored = getattr(user, "initials", None)
    if stored and str(stored).strip():
        custom = str(stored).strip().upper().replace("Ё", "Е")
    elif initials_map is not None and uid is not None:
        raw = initials_map.get(int(uid))
        if raw and str(raw).strip():
            custom = str(raw).strip().upper().replace("Ё", "Е")
    if custom and 3 <= len(custom) <= 8 and all(ch.isalpha() for ch in custom):
        return custom
    name = getattr(user, "display_name", None)
    email = getattr(user, "email", None)
    return initials_from_display_name(
        str(name).strip() if name else None,
        str(email).strip() if email else None,
    )


async def _fetch_initials_via_auth_internal(
    auth_user_ids: list[int] | None,
) -> dict[int, str | None] | None:
    settings = get_settings()
    secret = (os.environ.get("WS_INTERNAL_SECRET") or "").strip()
    base = (settings.auth_service_url or os.environ.get("AUTH_SERVICE_URL") or "").strip().rstrip("/")
    if not secret or not base:
        return None
    ids = sorted({int(x) for x in (auth_user_ids or []) if int(x) > 0})
    out: dict[int, str | None] = {}
    batches = [ids[i : i + 500] for i in range(0, len(ids), 500)] if ids else [[]]
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            for batch in batches:
                params = {"ids": ",".join(str(x) for x in batch)} if batch else {"ids": ""}
                if not batch:
                    continue
                response = await client.get(
                    f"{base}/internal/users/initials",
                    params=params,
                    headers={"X-Internal-Key": secret},
                )
                if response.status_code != 200:
                    _log.debug("auth internal initials: HTTP %s", response.status_code)
                    return None
                payload = response.json()
                if not isinstance(payload, dict):
                    return None
                for key, value in payload.items():
                    try:
                        out[int(key)] = value
                    except (TypeError, ValueError):
                        continue
    except httpx.RequestError as exc:
        _log.debug("auth internal initials lookup failed: %s", exc)
        return None
    return out


async def _fetch_initials_via_auth_db(
    auth_user_ids: list[int] | None,
) -> dict[int, str | None]:
    url = (os.environ.get("AUTH_DATABASE_URL") or "").strip()
    if not url:
        return {}
    engine = create_async_engine(make_async_url(url), echo=False)
    out: dict[int, str | None] = {}
    try:
        async with engine.connect() as conn:
            if auth_user_ids:
                ids = sorted({int(x) for x in auth_user_ids})
                for i in range(0, len(ids), 500):
                    batch = ids[i : i + 500]
                    result = await conn.execute(
                        text("SELECT id, initials FROM users WHERE id = ANY(:ids)"),
                        {"ids": batch},
                    )
                    for row in result.mappings():
                        out[int(row["id"])] = row["initials"]
            else:
                result = await conn.execute(text("SELECT id, initials FROM users"))
                for row in result.mappings():
                    out[int(row["id"])] = row["initials"]
    except Exception as exc:
        _log.debug("auth initials SQL fallback failed: %s", exc)
        return {}
    finally:
        await engine.dispose()
    return out


async def fetch_auth_initials_by_user_id(
    auth_user_ids: list[int] | None = None,
) -> dict[int, str | None]:
    via_api = await _fetch_initials_via_auth_internal(auth_user_ids)
    if via_api is not None:
        return via_api
    return await _fetch_initials_via_auth_db(auth_user_ids)
