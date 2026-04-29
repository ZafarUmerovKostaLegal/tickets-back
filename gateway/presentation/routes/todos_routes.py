

import sys

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response as FastAPIResponse

from infrastructure.config import get_settings
from infrastructure.upstream_auth_context import merge_upstream_headers

router = APIRouter(prefix="/api/v1/todos", tags=["todos"])

_TODOS_503_HINT = (
    "Gateway не достучался до микросервиса todos. Проверьте: "
    "1) контейнер todos запущен в одном стеке/сети с gateway; "
    "2) у gateway переменная TODOS_SERVICE_URL=http://todos:1240 (внутри Docker нельзя localhost/127.0.0.1 — это сам gateway); "
    "3) логи контейнера todos (БД, старт приложения). "
    "Диагностика: GET {gateway}/health/todos"
)


def _todos_base() -> str:
    return get_settings().todos_service_url.rstrip("/")


def _todos_upstream_503(
    base: str,
    exc: httpx.RequestError | None = None,
    *,
    extra: dict | None = None,
) -> JSONResponse:
    payload: dict = {
        "detail": "Todos service unavailable",
        "hint": _TODOS_503_HINT,
        "todos_service_url": base,
    }
    if exc is not None:
        payload["upstream_error"] = type(exc).__name__
        payload["upstream_message"] = str(exc)[:500]
        print(
            f"[gateway] todos upstream RequestError: base={base!r} {exc!r}",
            file=sys.stderr,
            flush=True,
        )
    if extra:
        payload.update(extra)
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


def _request_headers_for_todos_upstream(request: Request) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in request.headers.items():
        if key.lower() in _HOP_REQUEST_TO_UPSTREAM:
            continue
        out[key] = value
    return out


def _strip_hop_and_cors(headers: dict) -> dict:

    skip = {
        "transfer-encoding",
        "content-encoding",
        "connection",
        "keep-alive",
        "access-control-allow-origin",
        "access-control-allow-credentials",
        "access-control-allow-methods",
        "access-control-allow-headers",
        "access-control-expose-headers",
    }
    return {k: v for k, v in headers.items() if k.lower() not in skip}


@router.get("/calendar/connect", summary="Outlook: URL авторизации (всегда JSON для фронта)")
async def todos_calendar_connect(request: Request):

    base = _todos_base()
    if not base:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "TODOS_SERVICE_URL not configured",
                "hint": "Задайте TODOS_SERVICE_URL для gateway, например http://todos:1240",
            },
        )
    url = f"{base}/api/v1/todos/calendar/connect"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = merge_upstream_headers({}) or {}
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            r = await client.get(url, headers=headers)
    except httpx.RequestError as e:
        return _todos_upstream_503(base, e)
    except Exception:
        return JSONResponse(status_code=502, content={"detail": "Bad gateway"})
    if r.is_redirect:
        return JSONResponse(
            status_code=502,
            content={
                "detail": (
                    "Сервис todos вернул HTTP-редирект вместо JSON. "
                    "Обновите контейнер todos: GET /calendar/connect должен отдавать {\"url\": \"...\"}."
                )
            },
        )
    if r.status_code == 401:
        return JSONResponse(status_code=401, content={"detail": "Authorization required"})
    if r.status_code >= 400:
        try:
            body = r.json()
            if isinstance(body, dict):
                return JSONResponse(status_code=r.status_code, content=body)
        except Exception:
            pass
        return JSONResponse(
            status_code=r.status_code,
            content={"detail": (r.text or "Todos error")[:2000]},
        )
    try:
        data = r.json()
    except Exception:
        return JSONResponse(
            status_code=502,
            content={"detail": "Ответ todos /calendar/connect не JSON — ожидалось {\"url\": \"...\"}"},
        )
    if not isinstance(data, dict) or "url" not in data or not data["url"]:
        return JSONResponse(
            status_code=502,
            content={"detail": "В ответе todos нет поля url"},
        )
    return JSONResponse(content={"url": data["url"]})


@router.get("/calendar/status", summary="Outlook: статус подключения календаря (всегда JSON)")
async def todos_calendar_status(request: Request):

    base = _todos_base()
    if not base:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "TODOS_SERVICE_URL not configured",
                "hint": "Задайте TODOS_SERVICE_URL для gateway, например http://todos:1240",
                "connected": False,
            },
        )
    url = f"{base}/api/v1/todos/calendar/status"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    headers = merge_upstream_headers({}) or {}
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            r = await client.get(url, headers=headers)
    except httpx.RequestError as e:
        return _todos_upstream_503(base, e, extra={"connected": False})
    except Exception:
        return JSONResponse(
            status_code=502,
            content={"detail": "Bad gateway", "connected": False},
        )
    if r.status_code == 401:
        return JSONResponse(status_code=401, content={"detail": "Authorization required"})
    if r.status_code >= 400:
        connected = False
        detail: str | None = None
        try:
            body = r.json()
            if isinstance(body, dict):
                detail = body.get("detail")
                if "connected" in body:
                    connected = bool(body.get("connected"))
                return JSONResponse(
                    status_code=r.status_code,
                    content={
                        "detail": detail or "Todos calendar error",
                        "connected": connected,
                    },
                )
        except Exception:
            pass
        return JSONResponse(
            status_code=r.status_code,
            content={
                "detail": (r.text or "Todos error")[:2000],
                "connected": False,
            },
        )
    try:
        data = r.json()
    except Exception:
        return JSONResponse(
            status_code=502,
            content={
                "detail": "Ответ todos /calendar/status не JSON",
                "connected": False,
            },
        )
    if not isinstance(data, dict):
        return JSONResponse(
            status_code=502,
            content={"detail": "Некорректный JSON от todos", "connected": False},
        )
    connected = bool(data.get("connected"))
    out = {"connected": connected}
    if "error" in data:
        out["error"] = data["error"]
    return JSONResponse(content=out)


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy_todos(request: Request, path: str):

    base = _todos_base()
    if not base:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "TODOS_SERVICE_URL not configured",
                "hint": "Задайте TODOS_SERVICE_URL для gateway, например http://todos:1240",
            },
        )
    url = f"{base}/api/v1/todos/{path}" if path else f"{base}/api/v1/todos"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    raw_headers = _request_headers_for_todos_upstream(request)

    headers = merge_upstream_headers(raw_headers) or raw_headers
    try:
        body = await request.body()
    except Exception:
        body = b""
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            r = await client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body,
            )
    except httpx.RequestError as e:
        return _todos_upstream_503(base, e)
    except Exception:
        return JSONResponse(
            status_code=502,
            content={"detail": "Bad gateway"},
        )
    response_headers = _strip_hop_and_cors(dict(r.headers))
    return FastAPIResponse(
        content=r.content,
        status_code=r.status_code,
        headers=response_headers,
    )
