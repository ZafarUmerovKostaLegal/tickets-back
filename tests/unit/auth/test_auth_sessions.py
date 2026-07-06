

from application.session_policy import jtis_to_evict_after_register, session_jti_is_valid


def test_session_jti_is_valid_multi_device() -> None:
    assert session_jti_is_valid("a", ["a", "b"])
    assert session_jti_is_valid("b", ["a", "b"])
    assert not session_jti_is_valid("c", ["a", "b"])
    assert not session_jti_is_valid(None, ["a"])


def test_session_jti_is_valid_legacy_column() -> None:
    assert session_jti_is_valid("legacy", [], legacy_jti="legacy")
    assert not session_jti_is_valid("other", [], legacy_jti="legacy")


def test_session_jti_is_valid_no_sessions_legacy_tokens() -> None:
    assert session_jti_is_valid(None, [], legacy_jti=None)
    assert not session_jti_is_valid("x", [], legacy_jti=None)


def test_jtis_to_evict_after_register_fifo() -> None:
    assert jtis_to_evict_after_register(["a"], 2) == []
    assert jtis_to_evict_after_register(["a", "b"], 2) == ["a"]
    assert jtis_to_evict_after_register(["a", "b", "c"], 2) == ["a", "b"]
