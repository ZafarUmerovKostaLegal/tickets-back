from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from application.partner_report_confirmation_service import (
    _comment_to_out,
    _ensure_commentable_request_status,
    _normalize_comment_text,
    _request_to_out,
    can_bypass_partner_confirmation_gate,
)


def _sig(uid: int, when: datetime | None = None):
    return SimpleNamespace(
        partner_auth_user_id=uid,
        confirmed_at=when or datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


def _req(**kwargs):
    base = dict(
        id="r1",
        snapshot_id="s1",
        project_id="p1",
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 30),
        title="t",
        status="pending_partners",
        review_priority="yellow",
        submitted_by_auth_user_id=5,
        signatures=[],
        created_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        updated_at=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_request_to_out_pending_partners_and_priority_fallback():
    m = _req(review_priority="weird", signatures=[_sig(10)])
    out = _request_to_out(m, [10, 20], entry_count=0)
    assert out["reviewPriority"] == "yellow"
    assert out["pendingPartnerAuthUserIds"] == [20]
    assert out["requiredPartnerAuthUserIds"] == [10, 20]
    assert out["entryCount"] == 0
    assert out["isEmpty"] is True
    assert out["signatures"][0]["partnerAuthUserId"] == 10


def test_request_to_out_with_comments():
    comment = SimpleNamespace(
        id="c1",
        auth_user_id=7,
        text="hi",
        created_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )
    out = _request_to_out(_req(), [1], comments_count=1, last_comment=comment)
    assert out["commentsCount"] == 1
    assert out["lastComment"]["text"] == "hi"
    assert _comment_to_out(comment)["authUserId"] == 7


def test_normalize_comment_text():
    assert _normalize_comment_text("  ok  ") == "ok"
    with pytest.raises(HTTPException) as empty:
        _normalize_comment_text("   ")
    assert empty.value.status_code == 400
    with pytest.raises(HTTPException) as long:
        _normalize_comment_text("x" * 4001)
    assert long.value.status_code == 400


def test_ensure_commentable_status():
    _ensure_commentable_request_status("pending_partners")
    _ensure_commentable_request_status("fully_confirmed")
    with pytest.raises(HTTPException) as exc:
        _ensure_commentable_request_status("draft")
    assert exc.value.status_code == 409


def test_can_bypass_gate_admin():
    assert can_bypass_partner_confirmation_gate(
        {"id": 1, "role": "Главный администратор", "time_tracking_role": "user"}
    )
    assert not can_bypass_partner_confirmation_gate(
        {"id": 1, "role": "Сотрудник", "time_tracking_role": "user"}
    )
