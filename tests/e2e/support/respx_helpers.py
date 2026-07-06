from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any

import respx
from httpx import Request, Response


DEFAULT_USER: dict[str, Any] = {
    "id": 1,
    "email": "e2e@test.local",
    "display_name": "E2E User",
    "picture": None,
    "role": "Сотрудник",
    "time_tracking_role": "user",
    "permissions": {},
    "is_blocked": False,
    "is_archived": False,
    "created_at": "2024-01-01T00:00:00Z",
}

_USERS_ME_RE = re.compile(r"/users/me(?:\?|$)")
_TT_USER_RE = re.compile(r"/users/(\d+)(?:\?|$)")


def _json_response(body: Any, status: int = 200) -> Response:
    return Response(status, json=body)


def _upstream_handler(
    request: Request,
    *,
    me: dict[str, Any],
    body: Any,
    status: int,
) -> Response:
    path = request.url.path or ""
    if _USERS_ME_RE.search(path):
        return _json_response(me)
    match = _TT_USER_RE.search(path)
    if match and request.method.upper() == "GET":
        uid = int(match.group(1))
        return _json_response({"id": uid, "weekly_capacity_hours": 40})
    return _json_response(body, status=status)


@contextmanager
def upstream_mocks(*, user: dict[str, Any] | None = None, upstream_status: int = 200, upstream_body: Any = None):
    body = upstream_body if upstream_body is not None else []
    me = user if user is not None else DEFAULT_USER
    handler = lambda request: _upstream_handler(request, me=me, body=body, status=upstream_status)
    with respx.mock(assert_all_called=False) as router:
        router.route(url__regex=r"https?://[^/]+/.*").mock(side_effect=handler)
        yield router
