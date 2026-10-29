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
