

import os
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from service_path import ensure_service_in_path as _ensure_service_in_path


def pytest_configure(config):

    os.environ.setdefault("JWT_SECRET", "test-jwt-secret-min-32-characters-long")
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/test")
    os.environ.setdefault("TICKETS_SERVICE_URL", "http://tickets:1235")
    os.environ.setdefault("AUTH_SERVICE_URL", "http://auth:1236")
    os.environ.setdefault("NOTIFICATIONS_SERVICE_URL", "http://notifications:1237")
    os.environ.setdefault("INVENTORY_SERVICE_URL", "http://inventory:1238")
    os.environ.setdefault("ATTENDANCE_SERVICE_URL", "http://attendance:1239")
    os.environ.setdefault("TIME_TRACKING_SERVICE_URL", "http://time_tracking:1241")
    os.environ.setdefault("TODOS_SERVICE_URL", "http://todos:1240")
    os.environ.setdefault("GATEWAY_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/test")
    os.environ.setdefault("AUTH_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/test")
    os.environ.setdefault("TICKETS_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/test")
    os.environ.setdefault("NOTIFICATIONS_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/test")
    os.environ.setdefault("INVENTORY_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/test")
    os.environ.setdefault("ATTENDANCE_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/test")
    os.environ.setdefault("TODOS_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/test")
    os.environ.setdefault("TIME_TRACKING_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/test")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def gateway_client():

    _ensure_service_in_path("gateway")
    from presentation.api import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def auth_client():

    _ensure_service_in_path("auth")
    with patch("infrastructure.config.validate_production_secrets", lambda x: None):
        from presentation.api import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
async def tickets_client():

    _ensure_service_in_path("tickets")
    from presentation.api import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def notifications_client():

    _ensure_service_in_path("notifications")
    from presentation.api import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def inventory_client():

    _ensure_service_in_path("inventory")
    from presentation.api import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def attendance_client():

    _ensure_service_in_path("attendance")
    from presentation.api import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def time_tracking_client():

    _ensure_service_in_path("time_tracking")
    from presentation.api import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def todos_client():

    _ensure_service_in_path("todos")
    from presentation.api import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
