

from application.correspondence_service import (
    format_registry_number,
    normalize_doc_type,
    parse_status_filter,
    sniff_mime,
    validate_upload_content,
)


def test_format_registry_number_incoming():
    assert format_registry_number("incoming", 2026, 457) == "ВХ-2026/0457"


def test_format_registry_number_outgoing():
    assert format_registry_number("outgoing", 2026, 3) == "ИСХ-2026/0003"


def test_parse_status_group_work():
    assert parse_status_filter(None, "work") == ["approval", "pending_review", "progress"]


def test_normalize_review_statuses():
    from application.correspondence_service import normalize_status

    assert normalize_status("draft") == "draft"
    assert normalize_status("pending_review") == "pending_review"
    assert normalize_status("rejected") == "rejected"


def test_validate_pdf_magic():
    mime = validate_upload_content(b"%PDF-1.4 test", "application/octet-stream")
    assert mime == "application/pdf"


def test_validate_arbitrary_file():
    mime = validate_upload_content(b"PK\x03\x04", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_normalize_doc_type_default_letter():
    assert normalize_doc_type(None) == "letter"


def test_is_partner_org_role_variants():
    from application.correspondence_service import is_partner_org_role

    assert is_partner_org_role("Партнер") is True
    assert is_partner_org_role("Партнёр") is True
    assert is_partner_org_role("Сотрудник") is False


def test_sniff_jpeg():
    assert sniff_mime(b"\xff\xd8\xff\xe0", None) == "image/jpeg"
