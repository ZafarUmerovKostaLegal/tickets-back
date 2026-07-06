from application.user_initials import initials_from_display_name, resolve_user_initials


class _User:
    def __init__(self, *, auth_user_id: int, display_name: str | None = None, email: str = ""):
        self.auth_user_id = auth_user_id
        self.display_name = display_name
        self.email = email


def test_resolve_user_initials_prefers_auth_map():
    u = _User(auth_user_id=1, display_name="John Smith", email="j@x.com")
    assert resolve_user_initials(u, initials_map={1: "ZUM"}) == "ZUM"


def test_resolve_user_initials_falls_back_to_name():
    u = _User(auth_user_id=2, display_name="John Smith", email="j@x.com")
    assert resolve_user_initials(u, initials_map={}) == "JS"


def test_initials_from_display_name_single_word():
    assert initials_from_display_name("Alice", "alice@x.com") == "A"
