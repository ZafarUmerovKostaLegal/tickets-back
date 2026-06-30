from __future__ import annotations

import pytest
from fastapi import HTTPException

from application.team_validation import dedupe_member_ids


def test_dedupe_member_ids_ok() -> None:
    assert dedupe_member_ids([101, 102, 103]) == [101, 102, 103]


def test_dedupe_member_ids_rejects_duplicates() -> None:
    with pytest.raises(HTTPException) as exc:
        dedupe_member_ids([101, 101])
    assert exc.value.status_code == 400
