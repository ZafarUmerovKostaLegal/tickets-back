"""Приоритет проверки сформированных отчётов (хранится в БД)."""

from __future__ import annotations

from fastapi import HTTPException

REVIEW_PRIORITIES = frozenset({"red", "yellow", "green"})
DEFAULT_REVIEW_PRIORITY = "yellow"
REVIEW_PRIORITY_ORDER = ("red", "yellow", "green")


def normalize_review_priority(raw: str | None, *, required: bool = True) -> str | None:
    value = (raw or "").strip().lower()
    if not value:
        if required:
            raise HTTPException(status_code=400, detail="reviewPriority required")
        return None
    if value not in REVIEW_PRIORITIES:
        raise HTTPException(
            status_code=400,
            detail="Invalid reviewPriority (expected red, yellow or green)",
        )
    return value


def review_priority_rank(priority: str | None) -> int:
    value = (priority or DEFAULT_REVIEW_PRIORITY).strip().lower()
    try:
        return REVIEW_PRIORITY_ORDER.index(value)
    except ValueError:
        return REVIEW_PRIORITY_ORDER.index(DEFAULT_REVIEW_PRIORITY)


def sort_pending_by_review_priority(rows: list) -> list:
    """red → yellow → green, внутри — старше выше, затем id."""
    return sorted(
        rows,
        key=lambda m: (
            review_priority_rank(getattr(m, "review_priority", None)),
            getattr(m, "created_at", None) or 0,
            str(getattr(m, "id", "") or ""),
        ),
    )


def paginate_items(items: list, *, page: int, page_size: int) -> tuple[list, int, int, int]:
    total = len(items)
    safe_page = max(1, int(page))
    safe_size = max(1, min(200, int(page_size)))
    start = (safe_page - 1) * safe_size
    end = start + safe_size
    return items[start:end], safe_page, safe_size, total
