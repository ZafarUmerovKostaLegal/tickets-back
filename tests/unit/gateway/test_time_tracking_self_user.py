from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException

from application.time_tracking_self_user import (
    UserUpsertBody,
    build_self_time_tracking_user_upsert_payload,
    user_payload_bool,
)


def _body(**kwargs) -> UserUpsertBody:
    defaults: dict = {
        "auth_user_id": 42,
        "email": "u@example.com",
        "display_name": "U",
        "picture": None,
        "role": "wrong-from-client",
        "is_blocked": False,
        "is_archived": False,
        "weekly_capacity_hours": Decimal("40"),
    }
    defaults.update(kwargs)
    return UserUpsertBody(**defaults)


@pytest.mark.unit
def test_user_payload_bool_camel_and_snake():
    assert user_payload_bool({"is_blocked": True}, "is_blocked", "isBlocked") is True
    assert user_payload_bool({"isBlocked": 1}, "is_blocked", "isBlocked") is True
    assert user_payload_bool({}, "is_blocked", "isBlocked") is False


@pytest.mark.unit
def test_build_self_payload_uses_tt_role():
    user = {
        "id": 42,
        "email": "user@example.com",
        "time_tracking_role": "user",
        "position": "Юрист",
        "is_blocked": False,
        "is_archived": False,
    }
    payload = build_self_time_tracking_user_upsert_payload(user, _body(auth_user_id=42, role="manager"))
    assert payload["role"] == "user"


@pytest.mark.unit
def test_build_self_payload_requires_position():
    user = {"id": 42, "email": "u@x.y", "time_tracking_role": "user", "is_blocked": False, "is_archived": False}
    with pytest.raises(HTTPException) as exc:
        build_self_time_tracking_user_upsert_payload(user, _body(auth_user_id=42))
    assert exc.value.status_code == 400
