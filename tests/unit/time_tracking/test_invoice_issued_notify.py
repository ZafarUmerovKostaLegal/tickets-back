"""Notify accounting when an invoice is first issued to the client."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from support.service_path import ensure_service_in_path


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _Client:
    def __init__(self, lookup_status: int = 200, post_status: int = 201):
        self.lookup_status = lookup_status
        self.post_status = post_status
        self.posts: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url, **kwargs):
        assert "/internal/users/by-email" in url
        assert kwargs["params"]["email"] == "oidrisova@kostalegal.com"
        assert kwargs["headers"]["X-Internal-Key"] == "secret"
        if self.lookup_status != 200:
            return _Response(self.lookup_status, text="missing")
        return _Response(200, {"id": 42, "email": "oidrisova@kostalegal.com"})

    async def post(self, url, **kwargs):
        self.posts.append({"url": url, "json": kwargs["json"], "headers": kwargs["headers"]})
        return _Response(self.post_status, text="nope" if self.post_status >= 400 else "")


def _settings(**overrides):
    base = {
        "notify_invoice_sent_accounting": True,
        "invoice_sent_notify_to": "oidrisova@kostalegal.com",
        "notification_push_url": "http://gateway:1234/api/v1/notifications/system",
        "ws_internal_secret": "secret",
        "auth_service_url": "http://auth:1236",
        "frontend_url": "https://tickets.kostalegal.com",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "info@kostalegal.com",
        "smtp_password": "x",
        "smtp_use_tls": True,
        "mail_from": "info@kostalegal.com",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_only_a_draft_counts_as_the_first_issue():
    ensure_service_in_path("time_tracking")
    from infrastructure.invoice_issued_notify import is_first_client_issue

    assert is_first_client_issue("draft") is True
    assert is_first_client_issue(" sent ") is False
    assert is_first_client_issue("viewed") is False
    assert is_first_client_issue(None) is False


@pytest.mark.asyncio
async def test_first_issue_emails_oidrisova_and_posts_a_bell(monkeypatch):
    ensure_service_in_path("time_tracking")
    from infrastructure import invoice_issued_notify as notify

    client = _Client()
    monkeypatch.setattr(notify.httpx, "AsyncClient", lambda **kwargs: client)
    mailed: list[dict] = []

    async def fake_mail(settings, **kwargs):
        mailed.append(kwargs)
        return {"sent": True, "recipients": ["oidrisova@kostalegal.com"]}

    monkeypatch.setattr(notify, "send_invoice_issued_notice", fake_mail)

    await notify.notify_invoice_issued_to_accounting(
        _settings(),
        invoice_id="inv-1",
        invoice_number="KL-104",
        client_name="ADB Water",
        total_amount=Decimal("1788.8"),
        currency="usd",
    )

    assert len(client.posts) == 1
    body = client.posts[0]["json"]
    assert body["recipient_user_id"] == 42
    assert body["notification_type"] == "invoice_issued"
    assert "KL-104" in body["title"]
    assert "ADB Water" in body["description"]
    assert "1,788.80 USD" in body["description"]
    assert "https://tickets.kostalegal.com/time-tracking/invoices/inv-1" in body["description"]
    assert mailed[0]["invoice_number"] == "KL-104"
    assert mailed[0]["client_name"] == "ADB Water"
    assert mailed[0]["amount_label"] == "1,788.80 USD"
    assert mailed[0]["invoice_url"].endswith("/time-tracking/invoices/inv-1")


@pytest.mark.asyncio
async def test_missing_user_does_not_raise_and_mail_still_goes(monkeypatch):
    ensure_service_in_path("time_tracking")
    from infrastructure import invoice_issued_notify as notify

    monkeypatch.setattr(notify.httpx, "AsyncClient", lambda **kwargs: _Client(lookup_status=404))
    mailed: list[str] = []

    async def fake_mail(settings, **kwargs):
        mailed.append(kwargs["invoice_number"])
        return {"sent": True}

    monkeypatch.setattr(notify, "send_invoice_issued_notice", fake_mail)
    await notify.notify_invoice_issued_to_accounting(
        _settings(),
        invoice_id="inv-2",
        invoice_number="KL-2",
        client_name=None,
        total_amount=Decimal("10"),
        currency="UZS",
    )
    assert mailed == ["KL-2"]


@pytest.mark.asyncio
async def test_mail_failure_does_not_raise(monkeypatch):
    ensure_service_in_path("time_tracking")
    from infrastructure import invoice_issued_notify as notify

    monkeypatch.setattr(notify.httpx, "AsyncClient", lambda **kwargs: _Client())

    async def broken_mail(*args, **kwargs):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(notify, "send_invoice_issued_notice", broken_mail)
    await notify.notify_invoice_issued_to_accounting(
        _settings(),
        invoice_id="inv-3",
        invoice_number="KL-3",
        client_name="Client",
        total_amount=Decimal("1"),
        currency="USD",
    )


@pytest.mark.asyncio
async def test_issued_notice_is_skipped_when_smtp_or_flag_is_off(monkeypatch):
    ensure_service_in_path("time_tracking")
    from infrastructure import invoice_sent_mail

    sent: list[object] = []

    async def fake_send(*args, **kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(invoice_sent_mail.aiosmtplib, "send", fake_send)

    off = await invoice_sent_mail.send_invoice_issued_notice(
        _settings(notify_invoice_sent_accounting=False),
        invoice_number="KL-1",
        client_name="Client",
        amount_label="1.00 USD",
        invoice_url="",
    )
    assert off["sent"] is False
    assert off["skippedReason"] == "disabled"

    no_smtp = await invoice_sent_mail.send_invoice_issued_notice(
        _settings(smtp_host=""),
        invoice_number="KL-1",
        client_name="Client",
        amount_label="1.00 USD",
        invoice_url="",
    )
    assert no_smtp["skippedReason"] == "smtp_not_configured"
    assert sent == []


@pytest.mark.asyncio
async def test_issued_notice_sets_recipient_subject_and_link(monkeypatch):
    ensure_service_in_path("time_tracking")
    from infrastructure import invoice_sent_mail

    captured: list[object] = []

    async def fake_send(msg, **kwargs):
        captured.append(msg)

    monkeypatch.setattr(invoice_sent_mail.aiosmtplib, "send", fake_send)
    result = await invoice_sent_mail.send_invoice_issued_notice(
        _settings(),
        invoice_number="KL-104",
        client_name="ADB Water",
        amount_label="1,788.80 USD",
        invoice_url="https://tickets.kostalegal.com/time-tracking/invoices/inv-1",
    )
    assert result["sent"] is True
    assert result["recipients"] == ["oidrisova@kostalegal.com"]
    msg = captured[0]
    assert msg["To"] == "oidrisova@kostalegal.com"
    assert "KL-104" in msg["Subject"]
    payload = msg.get_payload()[0].get_payload(decode=True).decode("utf-8")
    assert "ADB Water" in payload
    assert "https://tickets.kostalegal.com/time-tracking/invoices/inv-1" in payload
