from application.partner_report_confirmation_service import (
    _comment_to_out,
    _normalize_comment_text,
    _request_to_out,
)
from fastapi import HTTPException
import pytest


def test_normalize_comment_text_trims_and_rejects_empty():
    assert _normalize_comment_text("  hello  ") == "hello"
    with pytest.raises(HTTPException) as empty:
        _normalize_comment_text("   ")
    assert empty.value.status_code == 400
    with pytest.raises(HTTPException) as too_long:
        _normalize_comment_text("x" * 4001)
    assert too_long.value.status_code == 400


def test_request_to_out_includes_comments_summary():
    class Sig:
        partner_auth_user_id = 2
        confirmed_at = __import__("datetime").datetime(2026, 7, 1, tzinfo=__import__("datetime").timezone.utc)

    class Req:
        id = "req-1"
        snapshot_id = "snap-1"
        project_id = "proj-1"
        date_from = __import__("datetime").date(2026, 1, 1)
        date_to = __import__("datetime").date(2026, 1, 31)
        title = "T"
        status = "fully_confirmed"
        submitted_by_auth_user_id = 1
        signatures = [Sig()]
        created_at = Sig.confirmed_at
        updated_at = Sig.confirmed_at

    class Comment:
        id = "c1"
        auth_user_id = 1
        text = "ok"
        created_at = Sig.confirmed_at

    out = _request_to_out(Req(), [2], comments_count=1, last_comment=Comment())
    assert out["commentsCount"] == 1
    assert out["lastComment"] == _comment_to_out(Comment())

    out_empty = _request_to_out(Req(), [2], comments_count=0, last_comment=None)
    assert out_empty["commentsCount"] == 0
    assert out_empty["lastComment"] is None
