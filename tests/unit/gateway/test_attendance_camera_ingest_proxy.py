import asyncio

import httpx
import pytest
from fastapi import HTTPException

from presentation.routes import attendance_routes as routes


class _Settings:
    attendance_service_url = "http://attendance"


def _patch_client(monkeypatch, seen: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"started": True})

    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(routes.httpx, "AsyncClient", factory)
    monkeypatch.setattr(routes, "get_settings", lambda: _Settings())


def test_backfill_forwards_only_given_params(monkeypatch):
    seen: dict = {}
    _patch_client(monkeypatch, seen)
    result = asyncio.run(routes.trigger_camera_ingest_backfill(
        date_from="2026-11-01", date_to="2026-11-20", year=None, _={"role": "Администратор"},
    ))
    assert result == {"started": True}
    # "year=" (empty) used to reach the service and fail with 422.
    assert seen["url"] == "http://attendance/hikvision/ingest/backfill?date_from=2026-11-01&date_to=2026-11-20"


def test_backfill_is_for_camera_data_managers_only(monkeypatch):
    seen: dict = {}
    _patch_client(monkeypatch, seen)
    with pytest.raises(HTTPException) as err:
        asyncio.run(routes.trigger_camera_ingest_backfill(
            date_from="2026-11-01", date_to="2026-11-20", year=None, _={"role": "Сотрудник"},
        ))
    assert err.value.status_code == 403
    assert "url" not in seen
