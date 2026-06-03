from __future__ import annotations

from application.manual_tt_users import (
    MANUAL_TT_USER_AUTH_ID_FLOOR,
    build_manual_tt_email,
    is_manual_tt_auth_user_id,
    slugify_manual_tt_local_part,
)


def test_is_manual_auth_user_id() -> None:
    assert is_manual_tt_auth_user_id(MANUAL_TT_USER_AUTH_ID_FLOOR)
    assert is_manual_tt_auth_user_id(MANUAL_TT_USER_AUTH_ID_FLOOR + 1)
    assert not is_manual_tt_auth_user_id(1)


def test_build_manual_tt_email() -> None:
    email = build_manual_tt_email("Aliye Ablyalimova")
    assert email.endswith("@manual.kostalegal.local")
    assert email.startswith("manual.aliye.ablyalimova@")


def test_slugify_manual_tt_local_part() -> None:
    assert slugify_manual_tt_local_part("  Ivan Petrov  ") == "ivan.petrov"
