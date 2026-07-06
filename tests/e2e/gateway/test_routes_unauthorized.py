from __future__ import annotations

from functools import lru_cache

import pytest

from e2e.support.route_discovery import RouteSpec, collect_routes, fill_path_params
from support.service_path import ensure_service_in_path


def _route_id(spec: RouteSpec) -> str:
    return f"{spec.method}:{spec.path}"


@lru_cache(maxsize=1)
def _gateway_auth_specs() -> tuple[RouteSpec, ...]:
    ensure_service_in_path("gateway")
    from presentation.api import app

    specs = collect_routes(app, service="gateway")
    return tuple(s for s in specs if s.method != "WEBSOCKET" and s.requires_auth)


_SPECS = _gateway_auth_specs()


@pytest.mark.e2e
@pytest.mark.parametrize("spec", _SPECS, ids=[_route_id(s) for s in _SPECS])
async def test_gateway_route_requires_auth(gateway_client, spec: RouteSpec):
    path = fill_path_params(spec.path)
    r = await gateway_client.request(spec.method, path)
    assert r.status_code in (401, 403), f"{spec.method} {path} -> {r.status_code}"
