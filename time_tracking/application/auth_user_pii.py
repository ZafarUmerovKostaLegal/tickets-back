"""Auth is the source of truth for PII of real users; TT keeps membership only.

Manual TT users (auth_user_id >= 2e9) keep local PII forever — they have no auth row.
Never deletes TT user rows or time entries.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from application.manual_tt_users import is_manual_tt_auth_user_id
from infrastructure.config import get_settings

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthUserPii:
    email: str
    display_name: str | None
    picture: str | None
    position: str | None
    initials: str | None = None


def stub_email_for_auth_user(auth_user_id: int) -> str:
    """Placeholder stored in TT until hydrated from auth on read."""
    return f"auth-user-{int(auth_user_id)}@tt.local"


class HydratedTtUser:
    """Read view: auth PII overlays TT row for non-manual users."""

    __slots__ = ("_row", "_pii")

    def __init__(self, row: Any, pii: AuthUserPii | None):
        self._row = row
        self._pii = pii

    @property
    def auth_user_id(self) -> int:
        return int(self._row.auth_user_id)

    @property
    def email(self) -> str:
        if self._pii and self._pii.email:
            return self._pii.email
        return str(getattr(self._row, "email", "") or "")

    @property
    def display_name(self) -> str | None:
        if self._pii and self._pii.display_name:
            return self._pii.display_name
        return getattr(self._row, "display_name", None)

    @property
    def picture(self) -> str | None:
        if self._pii is not None:
            return self._pii.picture
        return getattr(self._row, "picture", None)

    @property
    def position(self) -> str | None:
        # TT position stays local (auth sync must not overwrite / overlay it).
        return getattr(self._row, "position", None)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._row, name)


async def fetch_auth_pii_by_ids(auth_user_ids: list[int] | set[int]) -> dict[int, AuthUserPii]:
    ids = sorted({int(x) for x in auth_user_ids if int(x) > 0 and not is_manual_tt_auth_user_id(int(x))})
    if not ids:
        return {}
    settings = get_settings()
    secret = (os.environ.get("WS_INTERNAL_SECRET") or "").strip()
    base = (settings.auth_service_url or os.environ.get("AUTH_SERVICE_URL") or "").strip().rstrip("/")
    if not secret or not base:
        return {}
    out: dict[int, AuthUserPii] = {}
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            for i in range(0, len(ids), 500):
                batch = ids[i : i + 500]
                r = await client.get(
                    f"{base}/internal/users/by-ids",
                    params={"ids": ",".join(str(x) for x in batch)},
                    headers={"X-Internal-Key": secret},
                )
                if r.status_code != 200:
                    _log.debug("auth internal by-ids: HTTP %s", r.status_code)
                    return out
                data = r.json()
                if not isinstance(data, dict):
                    continue
                for key, raw in data.items():
                    if not isinstance(raw, dict):
                        continue
                    try:
                        uid = int(raw.get("id") or key)
                    except (TypeError, ValueError):
                        continue
                    email = str(raw.get("email") or "").strip()
                    if not email:
                        continue
                    dn = raw.get("display_name")
                    pic = raw.get("picture")
                    pos = raw.get("position")
                    ini = raw.get("initials")
                    out[uid] = AuthUserPii(
                        email=email,
                        display_name=str(dn).strip() if dn is not None and str(dn).strip() else None,
                        picture=str(pic).strip() if pic is not None and str(pic).strip() else None,
                        position=str(pos).strip() if pos is not None and str(pos).strip() else None,
                        initials=str(ini).strip() if ini is not None and str(ini).strip() else None,
                    )
    except httpx.RequestError as exc:
        _log.debug("auth internal by-ids failed: %s", exc)
    return out


def hydrate_tt_user(row: Any, pii_map: dict[int, AuthUserPii]) -> Any:
    if row is None:
        return None
    uid = int(getattr(row, "auth_user_id", 0) or 0)
    if is_manual_tt_auth_user_id(uid):
        return row
    return HydratedTtUser(row, pii_map.get(uid))


async def hydrate_users_map(users_map: dict[int, Any]) -> dict[int, Any]:
    if not users_map:
        return users_map
    pii = await fetch_auth_pii_by_ids(list(users_map.keys()))
    if not pii:
        return users_map
    return {uid: hydrate_tt_user(row, pii) for uid, row in users_map.items()}
