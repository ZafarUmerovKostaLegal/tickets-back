from application.company_expense_access import (
    can_act_on_company_expense,
    can_view_company_expense,
)


def test_only_named_emails_can_act():
    assert can_act_on_company_expense({"email": "Oidrisova@kostalegal.com"})
    assert can_act_on_company_expense({"email": "zumerov@kostalegal.com"})
    assert can_act_on_company_expense({"email": "aakhmadjonov@kostalegal.com"})
    assert not can_act_on_company_expense({"email": "partner@kostalegal.com", "role": "Партнер"})


def test_partners_can_view_but_not_act():
    partner = {"email": "other@kostalegal.com", "role": "Партнёр"}
    assert can_view_company_expense(partner)
    assert not can_act_on_company_expense(partner)
    assert not can_view_company_expense({"email": "staff@kostalegal.com", "role": "Сотрудник"})
