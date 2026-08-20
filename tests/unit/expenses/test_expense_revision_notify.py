from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from infrastructure import expense_author_decision_notify as notify_module


@pytest.mark.asyncio
async def test_revision_decision_notifies_author(monkeypatch):
    calls: list[dict] = []

    async def fake_notify(settings, **kwargs):
        calls.append(kwargs)

    async def fake_user(*_a, **_k):
        return {"id": 42, "email": "author@kostalegal.com", "display_name": "Author"}

    monkeypatch.setattr(notify_module, "notify_expense_author_decision", fake_notify)
    monkeypatch.setattr(notify_module, "fetch_user_by_id", fake_user)

    settings = SimpleNamespace(
        expense_notify_author_on_decision=True,
        expense_auth_bearer_for_author_email="",
        auth_service_url="http://auth",
    )

    await notify_module.run_author_decision_notification_safe(
        settings,
        authorization="Bearer x",
        author_user_id=42,
        expense_id="KL001303",
        decision="revision_required",
        reject_reason="Добавьте чек",
    )

    assert len(calls) == 1
    assert calls[0]["decision"] == "revision_required"
    assert calls[0]["reject_reason"] == "Добавьте чек"
    assert calls[0]["to_email"] == "author@kostalegal.com"
