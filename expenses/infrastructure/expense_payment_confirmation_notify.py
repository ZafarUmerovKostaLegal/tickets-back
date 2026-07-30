from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

import httpx

from infrastructure.auth_users import fetch_user_by_email_internal
from infrastructure.config import Settings
from infrastructure.expense_submit_mail import notify_expense_payment_confirmation_requested

_log = logging.getLogger(__name__)
_TIMEOUT_SEC = 90.0


async def _send_system_notification(
    settings: Settings,
    *,
    recipient_user_id: int,
    expense_id: str,
    amount_uzs: Decimal | None,
) -> None:
    url = (settings.notification_push_url or "").strip()
    secret = (settings.ws_internal_secret or "").strip()
    if not url or not secret:
        _log.warning(
            "expense payment system notify skipped: NOTIFICATION_PUSH_URL/WS_INTERNAL_SECRET not configured"
        )
        return
    amount = f"{amount_uzs:,.2f}".replace(",", " ") if amount_uzs is not None else "—"
    body = {
        "recipient_user_id": int(recipient_user_id),
        "title": f"Подтвердите оплату заявки {expense_id}",
        "description": (
            f"Возмещаемая заявка {expense_id} на сумму {amount} UZS одобрена. "
            "После фактической оплаты откройте заявку и нажмите «Оплачено»."
        ),
        "notification_type": "expense_payment_confirmation",
    }
    async with httpx.AsyncClient(timeout=8.0) as client:
        response = await client.post(url, json=body, headers={"X-Internal-Key": secret})
    if response.status_code >= 400:
        _log.warning(
            "expense payment system notify failed expense_id=%s status=%s body=%s",
            expense_id,
            response.status_code,
            (response.text or "")[:500],
        )


async def _run_payment_confirmation_notification(
    settings: Settings,
    *,
    expense_id: str,
    amount_uzs: Decimal | None,
    description: str | None,
    author_name: str | None,
) -> None:
    if not settings.expense_notify_payment_confirmer:
        return
    confirmer_email = (settings.expense_payment_confirmer_email or "").strip()
    if not confirmer_email:
        _log.warning("expense payment confirmation notify: confirmer email is empty")
        return

    try:
        await notify_expense_payment_confirmation_requested(
            settings,
            to_email=confirmer_email,
            expense_id=expense_id,
            amount_uzs=amount_uzs,
            description=description,
            author_name=author_name,
        )
    except Exception:
        _log.exception("expense payment confirmation email failed expense_id=%s", expense_id)

    profile = await fetch_user_by_email_internal(
        settings.auth_service_url,
        confirmer_email,
        internal_key=settings.ws_internal_secret,
    )
    if not profile or profile.get("id") is None:
        _log.warning(
            "expense payment system notify: user not found by email=%s expense_id=%s",
            confirmer_email,
            expense_id,
        )
        return
    try:
        await _send_system_notification(
            settings,
            recipient_user_id=int(profile["id"]),
            expense_id=expense_id,
            amount_uzs=amount_uzs,
        )
    except Exception:
        _log.exception("expense payment system notify failed expense_id=%s", expense_id)


async def run_payment_confirmation_notification_safe(
    settings: Settings,
    *,
    expense_id: str,
    amount_uzs: Decimal | None,
    description: str | None,
    author_name: str | None,
) -> None:
    try:
        await asyncio.wait_for(
            _run_payment_confirmation_notification(
                settings,
                expense_id=expense_id,
                amount_uzs=amount_uzs,
                description=description,
                author_name=author_name,
            ),
            timeout=_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        _log.error("expense payment confirmation notify timed out expense_id=%s", expense_id)
    except Exception:
        _log.exception("expense payment confirmation notify failed expense_id=%s", expense_id)
