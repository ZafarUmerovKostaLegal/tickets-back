from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

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


async def fetch_auth_initials_by_user_id(
    auth_user_ids: list[int] | None = None,
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
        _log.debug("auth initials lookup failed: %s", exc)
        return {}
    finally:
        await engine.dispose()
    return out
