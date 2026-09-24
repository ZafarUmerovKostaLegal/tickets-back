from __future__ import annotations

import sys

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response as FastAPIResponse

from infrastructure.config import get_settings
from infrastructure.upstream_auth_context import merge_upstream_headers

router = APIRouter(prefix="/api/v1/correspondence", tags=["correspondence"])

_CORRESPONDENCE_503_HINT = (
    "Gateway не достучался до микросервиса correspondence. Проверьте: "
    "1) контейнер correspondence запущен; "
    "2) CORRESPONDENCE_SERVICE_URL=http://correspondence:1249; "
    "3) GET /health/correspondence"
)


def _correspondence_base() -> str:
    return (get_settings().correspondence_service_url or "").rstrip("/")


def _correspondence_upstream_503(base: str, exc: httpx.RequestError | None = None) -> JSONResponse:
    payload: dict = {
        "detail": "Correspondence service unavailable",
        "hint": _CORRESPONDENCE_503_HINT,
        "correspondence_service_url": base,
    }
    if exc is not None:
        payload["upstream_error"] = type(exc).__name__
        payload["upstream_message"] = str(exc)[:500]
        print(
            f"[gateway] correspondence upstream RequestError: base={base!r} {exc!r}",
            file=sys.stderr,
            flush=True,
        )
    return JSONResponse(status_code=503, content=payload)


_HOP_REQUEST_TO_UPSTREAM = frozenset(
    {
        "host",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "trailer",
        "proxy-connection",
        "proxy-authenticate",
        "proxy-authorization",
        "upgrade",
        "content-length",
    }
)


def _request_headers_for_upstream(request: Request) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in request.headers.items():
        if key.lower() in _HOP_REQUEST_TO_UPSTREAM:
            continue
        out[key] = value
    return out


def _strip_hop_and_cors(headers: dict) -> dict:
    skip = {
        "transfer-encoding",
        "connection",
        "keep-alive",
        "content-encoding",
        "access-control-allow-origin",
        "access-control-allow-credentials",
        "access-control-allow-methods",
        "access-control-allow-headers",
    }
    return {k: v for k, v in headers.items() if k.lower() not in skip}


async def _forward_public(request: Request, path: str, *, timeout: float = 120.0):
    """Unauthenticated proxy for QR public-file links (do not require / forward login)."""
    base = _correspondence_base()
    if not base:
        return JSONResponse(status_code=503, content={"detail": "CORRESPONDENCE_SERVICE_URL is not configured"})
    upstream_url = f"{base}/api/v1/correspondence/{path}".rstrip("/")
    query = request.url.query
    if query:
        upstream_url = f"{upstream_url}?{query}"
    headers = _request_headers_for_upstream(request)
    for name in ("authorization", "Authorization", "cookie", "Cookie"):
        headers.pop(name, None)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            upstream = await client.request(
                request.method,
                upstream_url,
                headers=headers,
                content=None,
            )
    except httpx.RequestError as exc:
        return _correspondence_upstream_503(base, exc)
    out_headers = _strip_hop_and_cors(dict(upstream.headers))
    return FastAPIResponse(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=out_headers,
        media_type=upstream.headers.get("content-type"),
    )


@router.get("/{document_id}/public-card")
async def proxy_correspondence_public_card(document_id: str, request: Request):
    """Public verification page — no JWT, no session cookie forwarded."""
    return await _forward_public(request, f"{document_id}/public-card")


@router.get("/{document_id}/attachments/{attachment_id}/public-card")
async def proxy_correspondence_attachment_public_card(
    document_id: str,
    attachment_id: str,
    request: Request,
):
    return await _forward_public(
        request,
        f"{document_id}/attachments/{attachment_id}/public-card",
    )


@router.get("/{document_id}/public-file")
async def proxy_correspondence_public_file(document_id: str, request: Request):
    """Public QR download — no JWT (HMAC token in query)."""
    return await _forward_public(request, f"{document_id}/public-file")


@router.get("/{document_id}/attachments/{attachment_id}/public-file")
async def proxy_correspondence_attachment_public_file(
    document_id: str,
    attachment_id: str,
    request: Request,
):
    """Public QR download for a pinned attachment — no JWT."""
    return await _forward_public(
        request,
        f"{document_id}/attachments/{attachment_id}/public-file",
    )


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy_correspondence(request: Request, path: str):
    base = _correspondence_base()
    if not base:
        return JSONResponse(status_code=503, content={"detail": "CORRESPONDENCE_SERVICE_URL is not configured"})
    upstream_url = f"{base}/api/v1/correspondence/{path}".rstrip("/")
    if not path:
        upstream_url = f"{base}/api/v1/correspondence"
    query = request.url.query
    if query:
        upstream_url = f"{upstream_url}?{query}"
    headers = merge_upstream_headers(_request_headers_for_upstream(request))
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            upstream = await client.request(
                request.method,
                upstream_url,
                headers=headers,
                content=body if body else None,
            )
    except httpx.RequestError as exc:
        return _correspondence_upstream_503(base, exc)
    out_headers = _strip_hop_and_cors(dict(upstream.headers))
    if upstream.status_code == 204:
        return FastAPIResponse(status_code=204, headers=out_headers)
    return FastAPIResponse(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=out_headers,
        media_type=upstream.headers.get("content-type"),
    )
