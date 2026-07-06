from __future__ import annotations

import pytest

from support.service_path import ensure_service_in_path as _ensure_service_in_path
from e2e.support.route_discovery import RouteSpec, collect_routes


@pytest.fixture(scope="session")
def gateway_route_specs() -> list[RouteSpec]:
    _ensure_service_in_path("gateway")
    from presentation.api import app

    return collect_routes(app, service="gateway")


def _load_service_routes(service: str) -> list[RouteSpec]:
    _ensure_service_in_path(service)
    from presentation.api import app

    return collect_routes(app, service=service)


@pytest.fixture(scope="session")
def auth_route_specs() -> list[RouteSpec]:
    return _load_service_routes("auth")


@pytest.fixture(scope="session")
def tickets_route_specs() -> list[RouteSpec]:
    return _load_service_routes("tickets")


@pytest.fixture(scope="session")
def time_tracking_route_specs() -> list[RouteSpec]:
    return _load_service_routes("time_tracking")


@pytest.fixture(scope="session")
def expenses_route_specs() -> list[RouteSpec]:
    return _load_service_routes("expenses")


@pytest.fixture(scope="session")
def correspondence_route_specs() -> list[RouteSpec]:
    return _load_service_routes("correspondence")


@pytest.fixture(scope="session")
def vacation_route_specs() -> list[RouteSpec]:
    return _load_service_routes("vacation")


@pytest.fixture(scope="session")
def chat_route_specs() -> list[RouteSpec]:
    return _load_service_routes("chat")


@pytest.fixture(scope="session")
def todos_route_specs() -> list[RouteSpec]:
    return _load_service_routes("todos")


@pytest.fixture(scope="session")
def inventory_route_specs() -> list[RouteSpec]:
    return _load_service_routes("inventory")


@pytest.fixture(scope="session")
def attendance_route_specs() -> list[RouteSpec]:
    return _load_service_routes("attendance")


@pytest.fixture(scope="session")
def notifications_route_specs() -> list[RouteSpec]:
    return _load_service_routes("notifications")
