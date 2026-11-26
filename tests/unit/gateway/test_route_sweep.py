"""Every gateway route against a stub upstream: as an admin and as a plain employee.

The stub answers every upstream call with the same generic JSON, so response shapes do not match
real services and some routes fail on them; the sweep checks reachability and access rules, not payloads.
"""

from __future__ import annotations

import json
import re

import httpx
import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

ADMIN = {"id": 1, "email": "admin@kl.com", "role": "Главный администратор", "position": "Managing Partner",
         "time_tracking_role": "manager", "permissions": {}, "is_blocked": False, "is_archived": False}
EMPLOYEE = {"id": 7, "email": "emp@kl.com", "role": "Сотрудник", "position": "Associate",
            "time_tracking_role": "user", "permissions": {}, "is_blocked": False, "is_archived": False}

_SKIP = re.compile(r"/ws|/stream|/events|/sse|/camera-ingest|/upload-chunk", re.I)


def _stub_upstream(user: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/users/me") or path.endswith("/auth/me"):
            return httpx.Response(200, json=user)
        if request.method == "GET" and re.search(r"/(list|items|users|clients|projects|teams|entries|rates)$", path):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"id": 1, "items": [], "results": [], "total": 0, "ok": True})

    return httpx.MockTransport(handler)


@pytest.fixture()
def app_for(monkeypatch):
    from presentation.api import app

    real_client = httpx.AsyncClient

    def make(user: dict):
        transport = _stub_upstream(user)

        class StubClient(real_client):
            def __init__(self, *args, **kwargs):
                kwargs.pop("transport", None)
                kwargs.pop("verify", None)
                super().__init__(*args, transport=transport, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", StubClient)
        return app

    return make


def _routes(app):
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/") or _SKIP.search(route.path):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            yield method, route.path


def _fill(path: str) -> str:
    def value(match: re.Match) -> str:
        name = match.group(1).split(":")[0].lower()
        return "1" if name.endswith("id") and "user" in name else "abc123"

    return re.sub(r"\{([^}]+)\}", value, path)


async def _sweep(app, user):
    crashes = []
    statuses = {}
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for method, path in _routes(app):
            url = _fill(path)
            kwargs = {"headers": {"Authorization": "Bearer t"}}
            if method in ("POST", "PUT", "PATCH"):
                kwargs["content"] = json.dumps({})
                kwargs["headers"]["Content-Type"] = "application/json"
            response = await client.request(method, url, **kwargs)
            statuses[(method, path)] = response.status_code
            if response.status_code >= 500 and response.status_code not in (502, 503, 504):
                crashes.append((method, path, response.status_code, response.text[:200]))
    return statuses, crashes


async def test_admin_reaches_every_route(app_for):
    app = app_for(ADMIN)
    statuses, _crashes = await _sweep(app, ADMIN)
    assert len(statuses) > 100
    unauthenticated = sorted(key for key, code in statuses.items() if code == 401)
    assert unauthenticated == [], unauthenticated


async def test_employee_is_denied_on_admin_routes(app_for):
    app = app_for(EMPLOYEE)
    statuses, _crashes = await _sweep(app, EMPLOYEE)
    denied = {key for key, code in statuses.items() if code in (401, 403)}
    for key in (
        ("POST", "/api/v1/time-tracking/teams"),
        ("DELETE", "/api/v1/time-tracking/teams/{team_id}"),
        ("POST", "/api/v1/time-tracking/firm-bank-profiles"),
    ):
        assert key in denied, key
