"""Streaming reverse proxy for upstreams that carry files (file manager, Legal Hub disk).

Request and response bodies pass through chunk by chunk — a multi-gigabyte upload or download never sits in
gateway memory, so many people moving large files at once do not take the whole site down.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

TIMEOUT = httpx.Timeout(connect=10.0, read=900.0, write=900.0, pool=30.0)

_RESPONSE_SKIP = frozenset({
    "transfer-encoding",
    "connection",
    "keep-alive",
    "access-control-allow-origin",
    "access-control-allow-credentials",
    "access-control-allow-methods",
    "access-control-allow-headers",
})


async def stream_proxy(
    request: Request,
    upstream_url: str,
    headers: dict[str, str],
    on_error: Callable[[httpx.RequestError], JSONResponse],
) -> Response:
    length = request.headers.get("content-length")
    if length:
        headers["content-length"] = length
    has_body = request.method in {"POST", "PUT", "PATCH", "DELETE"} and (
        bool(length and length != "0") or "chunked" in request.headers.get("transfer-encoding", "").lower()
    )
    client = httpx.AsyncClient(timeout=TIMEOUT)
    try:
        upstream = await client.send(
            client.build_request(request.method, upstream_url, headers=headers,
                                 content=request.stream() if has_body else None),
            stream=True,
        )
    except httpx.RequestError as exc:
        await client.aclose()
        return on_error(exc)

    async def close() -> None:
        await upstream.aclose()
        await client.aclose()

    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _RESPONSE_SKIP}
    if upstream.status_code == 204:
        await close()
        return Response(status_code=204, headers={k: v for k, v in out_headers.items() if k.lower() != "content-length"})
    return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code, headers=out_headers,
                             background=BackgroundTask(close))
