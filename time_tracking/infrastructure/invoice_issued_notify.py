"""Когда счёт впервые отмечен как отправленный клиенту — письмо и колокольчик для бухгалтерии."""

from __future__ import annotations

import logging
from decimal import Decimal

import httpx

from infrastructure.config import Settings
from infrastructure.invoice_sent_mail import send_invoice_issued_notice

_log = logging.getLogger(__name__)


def is_first_client_issue(status: str | None) -> bool:
    """Уведомление уходит один раз: когда черновик впервые отмечают отправленным клиенту."""
    return (status or "").strip() == "draft"


def _money_label(amount: Decimal | None, currency: str | None) -> str:
    try:
        n = float(amount or 0)
    except (TypeError, ValueError):
        n = 0
    cur = (currency or "").strip().upper()
    text = f"{n:,.2f}"
    return f"{text} {cur}".strip()


async def _push_bell(
    settings: Settings,
    *,
    email: str,
    title: str,
    description: str,
) -> None:
    url = (settings.notification_push_url or "").strip()
    secret = (settings.ws_internal_secret or "").strip()
    auth = (settings.auth_service_url or "").strip().rstrip("/")
    if not url or not secret or not auth or not email:
        _log.warning("invoice issued bell skipped: push url, secret or auth is empty")
        return
    async with httpx.AsyncClient(timeout=8.0) as client:
        found = await client.get(
            f"{auth}/internal/users/by-email",
            params={"email": email.strip().lower()},
            headers={"X-Internal-Key": secret},
        )
        if found.status_code != 200:
            _log.warning("invoice issued bell: user lookup %s status=%s", email, found.status_code)
            return
        user_id = (found.json() or {}).get("id")
        if user_id is None:
            return
        posted = await client.post(
            url,
            json={
                "recipient_user_id": int(user_id),
                "title": title,
                "description": description,
                "notification_type": "invoice_issued",
            },
            headers={"X-Internal-Key": secret},
        )
        if posted.status_code >= 400:
            _log.warning(
                "invoice issued bell failed status=%s body=%s",
                posted.status_code,
                (posted.text or "")[:300],
            )


async def notify_invoice_issued_to_accounting(
    settings: Settings,
    *,
    invoice_id: str,
    invoice_number: str,
    client_name: str | None,
    total_amount: Decimal | None,
    currency: str | None,
) -> None:
    amount = _money_label(total_amount, currency)
    inv = (invoice_number or invoice_id or "").strip()
    client = (client_name or "").strip() or "—"
    base = (settings.frontend_url or "").strip().rstrip("/")
    link = f"{base}/time-tracking/invoices/{invoice_id}" if base and invoice_id else ""
    title = f"Счёт выставлен клиенту — {inv}"
    description = f"Клиент: {client}. Сумма: {amount}."
    if link:
        description = f"{description} {link}"

    for email in (settings.invoice_sent_notify_to or "").replace(";", ",").split(","):
        addr = email.strip()
        if "@" not in addr:
            continue
        try:
            await _push_bell(settings, email=addr, title=title, description=description)
        except Exception:
            _log.exception("invoice issued bell failed email=%s invoice=%s", addr, inv)

    try:
        await send_invoice_issued_notice(
            settings,
            invoice_number=inv,
            client_name=client,
            amount_label=amount,
            invoice_url=link,
        )
    except Exception:
        _log.exception("invoice issued mail failed invoice=%s", inv)
