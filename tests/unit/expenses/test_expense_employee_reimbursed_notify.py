from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from infrastructure import expense_author_decision_notify as notify_module


@pytest.mark.asyncio
async def test_employee_reimbursed_notifies_configured_recipients(monkeypatch):
    sent: list[dict] = []

    async def fake_mail(settings, **kwargs):
        sent.append(kwargs)

    async def fake_user(*args, **kwargs):
        return {"display_name": "Zafar Umerov", "email": "zumerov@kostalegal.com"}

    monkeypatch.setattr(notify_module, "notify_expense_employee_reimbursed", fake_mail)
    monkeypatch.setattr(notify_module, "fetch_user_by_id", fake_user)

    settings = SimpleNamespace(
        expense_notify_on_employee_reimbursed=True,
        expense_notify_reimbursement_to="oidrisova@kostalegal.com",
        expense_auth_bearer_for_author_email="",
        auth_service_url="http://auth:1236",
    )
    await notify_module._run_employee_reimbursed_notification(
        settings,
        authorization="Bearer x",
        author_user_id=12,
        expense_id="KL001308",
        description="Проверка отправки сообщения на почту",
        amount_uzs=Decimal("111111"),
        expense_date=date(2026, 9, 3),
        paid_by_user_id=3,
        paid_by_display_name="Tester",
        paid_by_email="tester@kostalegal.com",
    )
    assert len(sent) == 1
    assert sent[0]["to_email"] == "oidrisova@kostalegal.com"
    assert sent[0]["expense_id"] == "KL001308"
    assert sent[0]["author_name"] == "Zafar Umerov"
    assert "Tester" in sent[0]["paid_by_line"]


@pytest.mark.asyncio
async def test_employee_reimbursed_notify_can_be_disabled(monkeypatch):
    async def should_not_run(*args, **kwargs):
        raise AssertionError("notification must be skipped")

    monkeypatch.setattr(notify_module, "notify_expense_employee_reimbursed", should_not_run)
    settings = SimpleNamespace(
        expense_notify_on_employee_reimbursed=False,
        expense_notify_reimbursement_to="oidrisova@kostalegal.com",
    )
    await notify_module._run_employee_reimbursed_notification(
        settings,
        authorization=None,
        author_user_id=1,
        expense_id="KL001308",
        description="x",
        amount_uzs=Decimal("1"),
        expense_date=date(2026, 9, 3),
        paid_by_user_id=2,
        paid_by_display_name=None,
        paid_by_email=None,
    )
