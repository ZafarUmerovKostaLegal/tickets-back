from fastapi import FastAPI
from fastapi.testclient import TestClient

from presentation.middleware.path_guard import (
    PathGuardMiddleware,
    path_is_internal_only,
    path_is_suspicious,
)


def test_dot_segments_are_suspicious_in_any_encoding():
    assert path_is_suspicious("/api/v1/vacations/leave-requests/../schedule/employees/5")
    assert path_is_suspicious("/api/v1/vacations/leave-requests/%2E%2E/schedule/employees/5")
    assert path_is_suspicious("/api/v1/vacations/leave-requests/%252E%252E/schedule")
    assert path_is_suspicious("/api/v1/chat/./x")
    assert path_is_suspicious("/api/v1/files/a\\..\\b")
    assert path_is_suspicious("/api/v1/files/a%00b")


def test_normal_paths_pass():
    assert not path_is_suspicious("/api/v1/vacations/leave-requests/12")
    assert not path_is_suspicious("/api/v1/files/report..final.pdf")
    assert not path_is_suspicious("/api/v1/tickets/3f1c/comments")
    assert not path_is_suspicious("/")


def test_internal_only_paths():
    assert path_is_internal_only("/api/v1/expenses/report-data")
    assert path_is_internal_only("/api/v1/expenses//report-data")
    assert path_is_internal_only("/API/V1/expenses/Report-Data")
    assert path_is_internal_only("/api/v1/expenses/report-data%3Fx=1")
    assert not path_is_internal_only("/api/v1/expenses/123")
    assert not path_is_internal_only("/api/v1/expenses/report-data-other")


def _client() -> TestClient:
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "DELETE"])
    async def catch_all(path: str):
        return {"path": path}

    app.add_middleware(PathGuardMiddleware)
    return TestClient(app)


def test_middleware_blocks_vacation_self_service_bypass():
    client = _client()
    r = client.delete("/api/v1/vacations/leave-requests/%2E%2E/schedule/employees/5")
    assert r.status_code == 400
    r = client.get("/api/v1/expenses/report-data?dateFrom=2026-01-01&dateTo=2026-12-31")
    assert r.status_code == 404
    r = client.get("/api/v1/vacations/leave-requests/12")
    assert r.status_code == 200
