from types import SimpleNamespace

from application.auth_user_pii import AuthUserPii, HydratedTtUser, hydrate_tt_user, stub_email_for_auth_user
from application.manual_tt_users import MANUAL_TT_USER_AUTH_ID_FLOOR


def test_stub_email_for_auth_user():
    assert stub_email_for_auth_user(42) == "auth-user-42@tt.local"


def test_hydrated_tt_user_overlays_auth_pii():
    row = SimpleNamespace(
        auth_user_id=7,
        email="local@tt",
        display_name="Local",
        picture="local.png",
        position="TT Pos",
        role="user",
    )
    pii = AuthUserPii(
        email="auth@x",
        display_name="Auth Name",
        picture="auth.png",
        position="Auth Pos",
    )
    view = HydratedTtUser(row, pii)
    assert view.email == "auth@x"
    assert view.display_name == "Auth Name"
    assert view.picture == "auth.png"
    # TT position stays local
    assert view.position == "TT Pos"
    assert view.role == "user"


def test_hydrate_tt_user_skips_manual():
    row = SimpleNamespace(auth_user_id=MANUAL_TT_USER_AUTH_ID_FLOOR, email="m@x")
    assert hydrate_tt_user(row, {}) is row
