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
