from application.leave_pdf_copy import employee_role_genitive


def test_employee_role_starts_with_uppercase_for_known_role():
    assert employee_role_genitive("partner") == "Партнера"


def test_employee_role_starts_with_uppercase_for_custom_position():
    assert employee_role_genitive("senior associate") == "Senior associate"


def test_employee_role_preserves_existing_internal_capitalization():
    assert employee_role_genitive("Senior Associate") == "Senior Associate"


def test_employee_role_fallback_starts_with_uppercase():
    assert employee_role_genitive(None) == "Помощника"
