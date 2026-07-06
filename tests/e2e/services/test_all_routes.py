from __future__ import annotations

from functools import lru_cache

import pytest
from httpx import ASGITransport, AsyncClient

from e2e.support.bodies import request_body
from e2e.support.respx_helpers import upstream_mocks
from e2e.support.route_discovery import RouteSpec, collect_routes, fill_path_params
from support.service_path import ensure_service_in_path


SERVICE_APPS = (
    "auth",
    "tickets",
    "notifications",
    "inventory",
    "attendance",
    "time_tracking",
    "todos",
    "expenses",
    "vacation",
    "chat",
    "correspondence",
    "contacts",
    "call_schedule",
)


def _route_id(spec: RouteSpec) -> str:
    return f"{spec.service}:{spec.method}:{spec.path}"


@lru_cache(maxsize=1)
def _all_service_specs() -> tuple[RouteSpec, ...]:
    specs: list[RouteSpec] = []
    for name in SERVICE_APPS:
        try:
            ensure_service_in_path(name)
            if name == "auth":
                from unittest.mock import patch

                with patch("infrastructure.config.validate_production_secrets", lambda x: None):
                    from presentation.api import app
                    specs.extend(collect_routes(app, service=name))
            else:
                from presentation.api import app
                specs.extend(collect_routes(app, service=name))
        except Exception:
            continue
    return tuple(s for s in specs if s.method != "WEBSOCKET")


def _service_transport(service: str) -> ASGITransport:
    ensure_service_in_path(service)
    if service == "auth":
        from unittest.mock import patch

        patch("infrastructure.config.validate_production_secrets", lambda x: None).start()
    from presentation.api import app

    return ASGITransport(app=app)


_SPECS = _all_service_specs()


@pytest.mark.e2e
@pytest.mark.e2e_full
@pytest.mark.parametrize("spec", _SPECS, ids=[_route_id(s) for s in _SPECS])
async def test_service_route_smoke(spec: RouteSpec):
    transport = _service_transport(spec.service)
    path = fill_path_params(spec.path)
    body = request_body(spec.method, path)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        if spec.requires_auth:
            with upstream_mocks():
                r = await client.request(
                    spec.method,
                    path,
                    headers={"Authorization": "Bearer e2e-token"},
                    json=body,
                )
        else:
            r = await client.request(spec.method, path, json=body)
    assert r.status_code not in (500, 502), (
        f"{spec.service} {spec.method} {path} -> {r.status_code}: {r.text[:300]}"
    )
