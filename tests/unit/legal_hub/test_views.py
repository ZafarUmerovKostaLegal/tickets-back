import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from support.service_path import ensure_service_in_path


def test_task_views_split_mine_review_and_team():
    ensure_service_in_path("legal_hub")
    from application.views import task_in_view

    mine = dict(assignee_user_id=7, reviewer_user_id=3, user_id=7, view="mine")
    review = dict(assignee_user_id=7, reviewer_user_id=3, user_id=3, view="review")
    other = dict(assignee_user_id=7, reviewer_user_id=3, user_id=9, view="mine")
    team = dict(assignee_user_id=7, reviewer_user_id=None, user_id=9, view="team")

    assert task_in_view(**mine) is True
    assert task_in_view(**review) is True
    assert task_in_view(**other) is False
    assert task_in_view(**team) is True


def test_legal_hub_health_is_open_and_tasks_require_auth():
    ensure_service_in_path("legal_hub")
    from fastapi.testclient import TestClient
    from presentation.api import app

    with TestClient(app) as client:
        health = client.get("/health")
        tasks = client.get("/api/v1/legal-hub/tasks")

    assert health.status_code == 200
    assert health.json()["service"] == "legal_hub"
    assert tasks.status_code == 401
