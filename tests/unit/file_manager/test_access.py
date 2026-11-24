import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from support.service_path import ensure_service_in_path


def _access():
    ensure_service_in_path("file_manager")
    import application.access as access

    return access


def test_manager_roles():
    access = _access()
    assert access.is_manager("Администратор", None)
    assert access.is_manager("Сотрудник", "Managing Partner")
    assert access.is_manager("Партнёр", None)
    assert not access.is_manager("Юрист", "Associate")
    assert access.is_super_admin("Главный администратор")
    assert not access.is_super_admin("Партнер")


def test_resolve_matter_level_rules():
    access = _access()
    resolve = access.resolve_matter_level
    base = {"manager": False, "super_admin": False, "tt_access": False, "acl_level": None, "matter_closed": False}
    assert resolve(**base) == "none"
    assert resolve(**{**base, "tt_access": True}) == "edit"
    assert resolve(**{**base, "tt_access": True, "acl_level": "read"}) == "read"
    assert resolve(**{**base, "acl_level": "edit"}) == "edit"
    assert resolve(**{**base, "tt_access": True, "matter_closed": True}) == "read"
    assert resolve(**{**base, "manager": True}) == "manage"
    assert resolve(**{**base, "manager": True, "acl_level": "deny"}) == "none"
    assert resolve(**{**base, "manager": True, "super_admin": True, "acl_level": "deny"}) == "manage"
    assert resolve(**{**base, "tt_access": True, "acl_level": "deny"}) == "none"


def test_normalize_template_adds_parents_and_dedupes():
    access = _access()
    assert access.normalize_template(["Court/Hearings", "court", " ", "../x", "A\\B"]) == [
        "Court",
        "Court/Hearings",
        "x",
        "A",
        "A/B",
    ]
    assert access.normalize_template(None) == list(access.DEFAULT_TEMPLATE)


def test_file_kind_and_safe_name():
    access = _access()
    assert access.file_kind("Contract.DOCX") == "doc"
    assert access.file_kind("mail.msg") == "email"
    assert access.file_kind("scan.pdf") == "pdf"
    assert access.safe_name("C:\\tmp\\evil/../a.pdf") == "a.pdf"
    assert access.safe_name("") == "file"


def test_effective_grant_handles_temporary_and_suspended_entries():
    from datetime import datetime, timedelta, timezone

    from application.access import effective_grant

    at = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
    assert effective_grant("read", None, None, at) == "read"
    assert effective_grant("edit", at + timedelta(days=1), None, at) == "edit"
    assert effective_grant("edit", at - timedelta(minutes=1), None, at) is None
    assert effective_grant("edit", None, at + timedelta(hours=1), at) == "deny"
    assert effective_grant("edit", None, (at - timedelta(hours=1)).replace(tzinfo=None), at) == "edit"
    assert effective_grant(None, None, None, at) is None


def test_folder_levels_inherit_until_a_folder_breaks_the_chain():
    from application.access import folder_levels

    parents = {"a": None, "a1": "a", "a1x": "a1", "a2": "a", "b": None}
    assert folder_levels(parents, {"a": "read"}, set()) == {"a": "read", "a1": "read", "a1x": "read", "a2": "read"}
    assert folder_levels(parents, {"a": "read", "a1": "edit"}, set())["a1x"] == "edit"
    assert folder_levels(parents, {"a": "read"}, {"a1"}) == {"a": "read", "a2": "read"}
    closed = folder_levels(parents, {"a": "deny", "a1": "edit"}, set())
    assert closed["a1x"] == "deny" and closed["a2"] == "deny"
    assert folder_levels(parents, {"a": "deny", "a1": "edit"}, {"a1"})["a1x"] == "edit"


def test_folder_levels_own_resets_inherited_access():
    from application.access import folder_levels

    parents = {"a": None, "a1": "a", "a1x": "a1"}
    levels = folder_levels(parents, {"a": "read", "a1": "own"}, set())
    assert levels == {"a": "read", "a1": "own", "a1x": "own"}
