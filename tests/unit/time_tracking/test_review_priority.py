from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from application.review_priority import (
    normalize_review_priority,
    paginate_items,
    review_priority_rank,
    sort_pending_by_review_priority,
)


def test_normalize_review_priority_ok():
    assert normalize_review_priority("Red") == "red"
    assert normalize_review_priority("YELLOW") == "yellow"
    assert normalize_review_priority("green") == "green"


def test_normalize_review_priority_optional_empty():
    assert normalize_review_priority(None, required=False) is None
    assert normalize_review_priority("  ", required=False) is None


def test_normalize_review_priority_invalid():
    with pytest.raises(HTTPException) as exc:
        normalize_review_priority("blue")
    assert exc.value.status_code == 400


def test_normalize_review_priority_required_empty():
    with pytest.raises(HTTPException) as exc:
        normalize_review_priority("")
    assert exc.value.status_code == 400


def test_review_priority_rank_order():
    assert review_priority_rank("red") < review_priority_rank("yellow")
    assert review_priority_rank("yellow") < review_priority_rank("green")
    assert review_priority_rank("unknown") == review_priority_rank("yellow")


def test_sort_pending_by_review_priority():
    t0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    t1 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    rows = [
        SimpleNamespace(id="g-new", review_priority="green", created_at=t1),
        SimpleNamespace(id="y", review_priority="yellow", created_at=t0),
        SimpleNamespace(id="g-old", review_priority="green", created_at=t0),
        SimpleNamespace(id="r", review_priority="red", created_at=t1),
    ]
    sorted_ids = [m.id for m in sort_pending_by_review_priority(rows)]
    assert sorted_ids == ["r", "y", "g-old", "g-new"]


def test_paginate_items():
    items = list(range(5))
    page_items, page, size, total = paginate_items(items, page=2, page_size=2)
    assert page_items == [2, 3]
    assert page == 2
    assert size == 2
    assert total == 5


def test_paginate_items_clamps_page_size():
    items = list(range(10))
    _, page, size, total = paginate_items(items, page=0, page_size=500)
    assert page == 1
    assert size == 200
    assert total == 10
