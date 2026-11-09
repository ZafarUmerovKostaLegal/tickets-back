"""Proxy Outlook add-in auth endpoints to the auth microservice."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from infrastructure.config import get_settings

router = APIRouter(prefix="/api/v1/auth/outlook", tags=["auth-outlook"])


def _auth_base() -> str:
    return (get_settings().auth_service_url or "").rstrip("/")


@router.api_route("/{path:path}", methods=["GET", "POST", "OPTIONS"])
@router.api_route("", methods=["GET", "POST", "OPTIONS"])
async def proxy_outlook_auth(request: Request, path: str = ""):
    base = _auth_base()
    if not base:
        raise HTTPException(status_code=503, detail="Auth service URL is not configured")
    suffix = f"/{path}" if path else ""
    url = f"{base}/auth/outlook{suffix}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in {"host", "content-length", "connection"}
    }
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            upstream = await client.request(
                request.method,
                url,
                headers=headers,
                content=body if body else None,
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="Auth service unavailable") from exc
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
        headers={
            k: v
            for k, v in upstream.headers.items()
            if k.lower() not in {"content-encoding", "transfer-encoding", "connection", "content-length"}
        },
    )
