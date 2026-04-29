

from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException


def service_base_url(raw_url: str | None, service_name: str) -> str:
    base = (raw_url or "").strip().rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail=f"{service_name} service not configured")
    return base


def upstream_error_detail(response: httpx.Response, fallback: str) -> str:
    text = response.text.strip()
    if not text:
        return fallback
    try:
        payload = response.json()
    except ValueError:
        return text
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail
    return text or fallback


async def send_upstream_request(
    method: str,
    url: str,
    *,
    timeout: float,
    unavailable_status: int,
    unavailable_detail: str,
    **kwargs: Any,
) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.request(method, url, **kwargs)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=unavailable_status, detail=unavailable_detail) from exc


def raise_for_upstream_status(
    response: httpx.Response,
    fallback_detail: str,
    *,
    status_detail_map: dict[int, str] | None = None,
) -> None:
    if response.status_code < 400:
        return
    mapped_detail = (status_detail_map or {}).get(response.status_code)
    detail = mapped_detail or upstream_error_detail(response, fallback_detail)
    raise HTTPException(status_code=response.status_code, detail=detail)
