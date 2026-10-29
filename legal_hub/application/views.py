from __future__ import annotations

TASK_STATUSES = frozenset({
    "in_progress",
    "waiting_external",
    "pending_review",
    "in_review",
    "changes_required",
    "reviewing",
})

TASK_VIEWS = frozenset({"mine", "review", "team"})


def task_in_view(
    *,
    assignee_user_id: int,
    reviewer_user_id: int | None,
    user_id: int,
    view: str,
) -> bool:
    if view == "team":
        return True
    if view == "mine":
        return assignee_user_id == user_id
    if view == "review":
        return reviewer_user_id == user_id
    raise ValueError(view)
