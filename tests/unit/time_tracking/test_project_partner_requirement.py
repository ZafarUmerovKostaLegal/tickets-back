from application.project_partner_requirement import (
    job_title_indicates_partner,
    merged_position_tt_auth,
    org_role_indicates_partner,
    user_satisfies_partner_rule,
)


def test_job_title_partner_ru_en():
    assert job_title_indicates_partner("Партнер")
    assert job_title_indicates_partner("Junior Partner")
    assert not job_title_indicates_partner("Юрист")
    assert not job_title_indicates_partner(None)
    assert not job_title_indicates_partner("  ")


def test_org_role_partner():
    assert org_role_indicates_partner("Партнер")
    assert org_role_indicates_partner("Partner Counsel")
    assert not org_role_indicates_partner("Сотрудник")


def test_merged_position_prefers_tt():
    assert merged_position_tt_auth(" TT ", "auth") == "TT"
    assert merged_position_tt_auth(None, " auth ") == "auth"
    assert merged_position_tt_auth(None, None) is None


def test_user_satisfies_partner_rule():
    assert user_satisfies_partner_rule("Партнер", None, None)
    assert user_satisfies_partner_rule(None, None, "Партнер")
    assert not user_satisfies_partner_rule("Associate", None, "Employee")
