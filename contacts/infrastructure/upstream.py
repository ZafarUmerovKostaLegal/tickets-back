from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException


class UpstreamError(HTTPException):
    pass


async def auth_get(path: str, *, authorization: str, timeout: float = 15.0) -> Any:
    from infrastructure.config import get_settings

    base = get_settings().auth_service_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"{base}{path}", headers={"Authorization": authorization})
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="Auth service unavailable") from exc
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    return r.json()


async def tt_request(
    method: str,
    path: str,
    *,
    authorization: str,
    params: dict[str, str] | None = None,
    json_body: dict | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    from infrastructure.config import get_settings

    base = get_settings().time_tracking_service_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.request(
                method,
                f"{base}{path}",
                headers={"Authorization": authorization},
                params=params,
                json=json_body,
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="Time tracking service unavailable") from exc


async def tt_json(
    method: str,
    path: str,
    *,
    authorization: str,
    params: dict[str, str] | None = None,
    json_body: dict | None = None,
) -> Any:
    r = await tt_request(
        method,
        path,
        authorization=authorization,
        params=params,
        json_body=json_body,
    )
    if r.status_code >= 400:
        try:
            detail = r.json()
        except Exception:
            detail = r.text or "Time tracking service error"
        raise HTTPException(status_code=r.status_code, detail=detail)
    if r.status_code == 204:
        return None
    if not r.content:
        return None
    return r.json()
