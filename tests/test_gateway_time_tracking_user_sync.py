

from decimal import Decimal

import pytest
from fastapi import HTTPException

from service_path import ensure_service_in_path

ensure_service_in_path("gateway")

from presentation.routes.time_tracking_routes import (
    UserUpsertBody,
    _self_time_tracking_user_upsert_payload,
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


def test_self_payload_uses_time_tracking_role_not_body_role():
    user = {
        "id": 42,
        "email": "user@example.com",
        "display_name": "Иван",
        "time_tracking_role": "user",
        "is_blocked": False,
        "is_archived": False,
    }
    body = _body(auth_user_id=42, role="manager")
    payload = _self_time_tracking_user_upsert_payload(user, body)
    assert payload["auth_user_id"] == 42
    assert payload["role"] == "user"
    assert payload["email"] == "user@example.com"
    assert payload["weekly_capacity_hours"] is not None


def test_self_payload_manager_from_auth_camel_case():
    user = {
        "id": 7,
        "email": "m@example.com",
        "displayName": "Мария",
        "timeTrackingRole": "manager",
        "is_blocked": False,
        "is_archived": False,
    }
    body = _body(auth_user_id=7, email="", role="user")
    payload = _self_time_tracking_user_upsert_payload(user, body)
    assert payload["role"] == "manager"
    assert payload["email"] == "m@example.com"


def test_self_payload_denied_without_tt_role():
    user = {
        "id": 1,
        "email": "x@y.z",
        "time_tracking_role": None,
        "is_blocked": False,
        "is_archived": False,
    }
    body = _body(auth_user_id=1)
    with pytest.raises(HTTPException) as ei:
        _self_time_tracking_user_upsert_payload(user, body)
    assert ei.value.status_code == 403


def test_self_payload_blocked_flags_from_auth_not_body():
    user = {
        "id": 3,
        "email": "b@blocked.com",
        "time_tracking_role": "user",
        "isBlocked": True,
        "isArchived": False,
    }
    body = _body(auth_user_id=3, is_blocked=False, is_archived=False)
    payload = _self_time_tracking_user_upsert_payload(user, body)
    assert payload["is_blocked"] is True
    assert payload["is_archived"] is False


def test_self_payload_includes_position_from_auth():
    user = {
        "id": 42,
        "email": "user@example.com",
        "display_name": "Иван",
        "time_tracking_role": "user",
        "position": "  Юрист  ",
        "is_blocked": False,
        "is_archived": False,
    }
    body = _body(auth_user_id=42)
    payload = _self_time_tracking_user_upsert_payload(user, body)
    assert payload["position"] == "Юрист"


def test_self_payload_position_absent_becomes_none():
    user = {
        "id": 42,
        "email": "user@example.com",
        "time_tracking_role": "user",
        "is_blocked": False,
        "is_archived": False,
    }
    body = _body(auth_user_id=42)
    payload = _self_time_tracking_user_upsert_payload(user, body)
    assert payload.get("position") is None
