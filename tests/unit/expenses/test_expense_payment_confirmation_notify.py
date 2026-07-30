from decimal import Decimal
from types import SimpleNamespace

import pytest

from infrastructure import expense_payment_confirmation_notify as notify_module
from presentation.routes.expense_email_action import _confirm_html


@pytest.mark.asyncio
async def test_approved_reimbursable_notifies_confirmer_by_email_and_in_app(monkeypatch):
    calls: list[tuple[str, object]] = []

    async def fake_email(settings, **kwargs):
        calls.append(("email", kwargs))

    async def fake_user(*args, **kwargs):
        calls.append(("lookup", kwargs))
        return {"id": 77, "email": "aakhmadjonov@kostalegal.com"}

    async def fake_system(settings, **kwargs):
        calls.append(("system", kwargs))

    monkeypatch.setattr(notify_module, "notify_expense_payment_confirmation_requested", fake_email)
    monkeypatch.setattr(notify_module, "fetch_user_by_email_internal", fake_user)
    monkeypatch.setattr(notify_module, "_send_system_notification", fake_system)

    settings = SimpleNamespace(
        expense_notify_payment_confirmer=True,
        expense_payment_confirmer_email="aakhmadjonov@kostalegal.com",
        auth_service_url="http://auth:1236",
        ws_internal_secret="secret",
    )
    await notify_module._run_payment_confirmation_notification(
        settings,
        expense_id="KL-2026-00042",
        amount_uzs=Decimal("1500000"),
        description="Такси",
        author_name="User",
    )

    assert [kind for kind, _ in calls] == ["email", "lookup", "system"]
    assert calls[0][1]["to_email"] == "aakhmadjonov@kostalegal.com"
    assert calls[2][1]["recipient_user_id"] == 77


@pytest.mark.asyncio
async def test_payment_confirmation_notification_can_be_disabled(monkeypatch):
    async def should_not_run(*args, **kwargs):
        raise AssertionError("notification must be skipped")

    monkeypatch.setattr(notify_module, "notify_expense_payment_confirmation_requested", should_not_run)
    settings = SimpleNamespace(
        expense_notify_payment_confirmer=False,
        expense_payment_confirmer_email="aakhmadjonov@kostalegal.com",
    )
    await notify_module._run_payment_confirmation_notification(
        settings,
        expense_id="KL-2026-00042",
        amount_uzs=Decimal("1500000"),
        description="Такси",
        author_name="User",
    )


def test_email_reject_confirmation_requires_visible_reason_field():
    page = _confirm_html(
        expense_id="KL-2026-00042",
        action="reject",
        final_url="https://tickets.example/api/v1/expenses/KL-2026-00042/email-action?token=signed",
        cancel_hint="Закройте вкладку",
    )
    assert 'name="reason"' in page
    assert "required" in page
    assert 'name="token" value="signed"' in page
