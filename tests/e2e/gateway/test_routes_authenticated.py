from __future__ import annotations

from functools import lru_cache

import pytest

from e2e.support.bodies import request_body
from e2e.support.respx_helpers import upstream_mocks
from e2e.support.route_discovery import RouteSpec, collect_routes, fill_path_params
from support.service_path import ensure_service_in_path


def _route_id(spec: RouteSpec) -> str:
    return f"{spec.method}:{spec.path}"


@lru_cache(maxsize=1)
def _gateway_specs() -> tuple[RouteSpec, ...]:
    ensure_service_in_path("gateway")
    from presentation.api import app

    return tuple(s for s in collect_routes(app, service="gateway") if s.method != "WEBSOCKET")


_SPECS = _gateway_specs()


@pytest.mark.e2e
@pytest.mark.e2e_full
@pytest.mark.parametrize("spec", _SPECS, ids=[_route_id(s) for s in _SPECS])
async def test_gateway_route_authenticated(gateway_client, spec: RouteSpec):
    path = fill_path_params(spec.path)
    body = request_body(spec.method, path)
    with upstream_mocks():
        r = await gateway_client.request(
            spec.method,
            path,
            headers={"Authorization": "Bearer e2e-token"},
            json=body,
        )
    assert r.status_code not in (401, 403), (
        f"{spec.method} {path} -> {r.status_code}: {r.text[:300]}"
    )
