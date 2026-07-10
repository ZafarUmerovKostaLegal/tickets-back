from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import HTTPException

from infrastructure.auth_upstream import auth_service_request
from infrastructure.config import get_settings
from infrastructure.upstream_http import service_base_url

_log = logging.getLogger(__name__)

_TT_ROLES = frozenset({"user", "manager"})


def _time_tracking_base_url() -> str:
    return service_base_url(get_settings().time_tracking_service_url, "Time tracking")


def _auth_headers(authorization: str | None) -> dict[str, str]:
    authz = (authorization or "").strip()
    if not authz:
        return {}
    if not authz.lower().startswith("bearer "):
        authz = f"Bearer {authz}"
    return {"Authorization": authz}


def _tt_role_from_record(record: dict, *, default_tt_role: str = "user") -> str:
    raw = (record.get("time_tracking_role") or record.get("timeTrackingRole") or "") or ""
    role = str(raw).strip()
    if role in _TT_ROLES:
        return role
    fallback = (default_tt_role or "").strip()
    return fallback if fallback in _TT_ROLES else "user"


def _bool_from_record(record: dict, snake: str, camel: str) -> bool:
    value = record.get(snake)
    if value is None:
        value = record.get(camel)
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes"}


def build_tt_upsert_payload_from_auth_record(
    record: dict,
    *,
    auth_user_id: int | None = None,
    default_tt_role: str = "user",
) -> dict[str, Any] | None:
    uid_raw = auth_user_id if auth_user_id is not None else record.get("id")
    if uid_raw is None:
        return None
    uid = int(uid_raw)
    email = (str(record.get("email") or "").strip())
    if not email:
        return None

    disp = record.get("display_name")
    if disp is None:
        disp = record.get("displayName")
    display_name = str(disp).strip() if disp is not None and str(disp).strip() else None

    pic = record.get("picture")
    picture = str(pic).strip() if pic is not None and str(pic).strip() else None

    pos = record.get("position")
    position = str(pos).strip() if pos is not None and str(pos).strip() else None

    return {
        "auth_user_id": uid,
        "email": email,
        "display_name": display_name,
        "picture": picture,
        "position": position,
        "role": _tt_role_from_record(record, default_tt_role=default_tt_role),
        "is_blocked": _bool_from_record(record, "is_blocked", "isBlocked"),
        "is_archived": _bool_from_record(record, "is_archived", "isArchived"),
    }


async def fetch_auth_user_record_for_tt(
    auth_user_id: int,
    authorization: str | None,
    *,
    default_tt_role: str = "user",
) -> dict | None:
    authz_headers = _auth_headers(authorization)
    if not authz_headers:
        return None

    detail = await auth_service_request(
        "GET",
        f"/users/{int(auth_user_id)}",
        authorization,
        timeout=15.0,
    )
    if detail.status_code == 200:
        data = detail.json()
        return data if isinstance(data, dict) else None

    public = await auth_service_request(
        "GET",
        f"/users/{int(auth_user_id)}/public",
        authorization,
        timeout=15.0,
    )
    if public.status_code != 200:
        return None
    data = public.json()
    if not isinstance(data, dict):
        return None
    merged = dict(data)
    if not _tt_role_from_record(merged, default_tt_role=""):
        merged["time_tracking_role"] = default_tt_role
    return merged


async def _tt_user_exists(auth_user_id: int, authorization: str | None) -> bool:
    base = _time_tracking_base_url()
    headers = _auth_headers(authorization)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{base}/users/{int(auth_user_id)}", headers=headers)
    except httpx.RequestError as exc:
        _log.debug("tt user lookup %s failed: %s", auth_user_id, exc)
        return False
    return r.status_code == 200


async def upsert_time_tracking_user_from_auth_record(
    record: dict,
    authorization: str | None,
) -> None:
    uid = record.get("id")
    if uid is None:
        return

    tt_role = _tt_role_from_record(record, default_tt_role="")
    base = _time_tracking_base_url()
    if not base:
        return
    auth_headers = _auth_headers(authorization)
    is_blocked = _bool_from_record(record, "is_blocked", "isBlocked")
    is_archived = _bool_from_record(record, "is_archived", "isArchived")

    async with httpx.AsyncClient(timeout=15.0) as client:
        if tt_role in _TT_ROLES:
            pos = record.get("position")
            pos_s = str(pos).strip() if pos is not None and str(pos).strip() else None
            if not pos_s:
                # Critical dual-write fix: still sync block/archive flags without full upsert.
                r = await client.patch(
                    f"{base}/users/{int(uid)}/lifecycle-flags",
                    json={"isBlocked": is_blocked, "isArchived": is_archived},
                    headers=auth_headers,
                )
                if r.status_code in (200, 404):
                    return
                detail = (r.text or "").strip()
                if len(detail) > 500:
                    detail = detail[:500]
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"Не удалось синхронизировать флаги пользователя с Time Tracking: "
                        f"HTTP {r.status_code}. {detail or 'Пустой ответ upstream'}"
                    ),
                )
            payload = build_tt_upsert_payload_from_auth_record(record, default_tt_role=tt_role)
            if not payload:
                return
            r = await client.post(f"{base}/users", json=payload, headers=auth_headers)
            if r.status_code >= 400:
                detail = (r.text or "").strip()
                if len(detail) > 500:
                    detail = detail[:500]
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"Не удалось синхронизировать пользователя с Time Tracking: "
                        f"HTTP {r.status_code}. {detail or 'Пустой ответ upstream'}"
                    ),
                )
            return

        r = await client.delete(f"{base}/users/{int(uid)}", headers=auth_headers)
        if r.status_code not in (200, 404):
            detail = (r.text or "").strip()
            if len(detail) > 500:
                detail = detail[:500]
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Не удалось удалить пользователя из Time Tracking: "
                    f"HTTP {r.status_code}. {detail or 'Пустой ответ upstream'}"
                ),
            )


async def provision_time_tracking_user_from_auth(
    auth_user_id: int,
    authorization: str | None,
    *,
    default_tt_role: str = "user",
    require_tt_role: bool = False,
) -> bool:
    """Create/update TT user from auth profile. Returns True when user exists in TT."""

    if await _tt_user_exists(auth_user_id, authorization):
        return True

    record = await fetch_auth_user_record_for_tt(
        auth_user_id,
        authorization,
        default_tt_role=default_tt_role,
    )
    if not record:
        return False

    tt_role = _tt_role_from_record(record, default_tt_role=default_tt_role)
    if require_tt_role and tt_role not in _TT_ROLES:
        return False

    payload = build_tt_upsert_payload_from_auth_record(
        record,
        auth_user_id=auth_user_id,
        default_tt_role=default_tt_role,
    )
    if not payload:
        return False

    base = _time_tracking_base_url()
    headers = _auth_headers(authorization)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{base}/users", json=payload, headers=headers)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Time tracking service unavailable while provisioning user {auth_user_id}: {exc}",
        ) from exc

    if r.status_code >= 400:
        detail = (r.text or "").strip()
        if len(detail) > 500:
            detail = detail[:500]
        raise HTTPException(
            status_code=503,
            detail=(
                f"Не удалось добавить пользователя id={auth_user_id} в учёт времени: "
                f"HTTP {r.status_code}. {detail or 'Пустой ответ upstream'}"
            ),
        )
    return True


async def sync_eligible_auth_users_to_time_tracking(authorization: str | None) -> None:
    """Provision all auth users with TT role so they appear in TT without visiting the module."""

    authz_headers = _auth_headers(authorization)
    if not authz_headers:
        return

    r = await auth_service_request(
        "GET",
        "/users",
        authorization,
        timeout=30.0,
        params={"include_archived": "true"},
    )
    if r.status_code >= 400:
        _log.debug("auth users list for TT sync: HTTP %s", r.status_code)
        return

    rows = r.json()
    if not isinstance(rows, list):
        return

    for row in rows:
        if not isinstance(row, dict):
            continue
        uid = row.get("id")
        if uid is None:
            continue
        tt_role = _tt_role_from_record(row, default_tt_role="")
        if tt_role not in _TT_ROLES:
            continue
        try:
            await provision_time_tracking_user_from_auth(
                int(uid),
                authorization,
                default_tt_role=tt_role,
            )
        except HTTPException:
            raise
        except Exception as exc:
            _log.warning("skip TT sync for auth user %s: %s", uid, exc)


async def provision_time_tracking_users_for_project_members(
    auth_user_ids: list[int],
    authorization: str | None,
) -> None:
    seen: set[int] = set()
    for raw in auth_user_ids:
        uid = int(raw)
        if uid in seen:
            continue
        seen.add(uid)
        ok = await provision_time_tracking_user_from_auth(uid, authorization)
        if not ok:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Не удалось подготовить пользователя id={uid} для учёта времени. "
                    "Проверьте, что у него есть email и роль в учёте времени."
                ),
            )
