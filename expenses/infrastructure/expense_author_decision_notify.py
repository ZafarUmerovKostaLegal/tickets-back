

from __future__ import annotations

import asyncio
import logging
from typing import Literal, Optional

from infrastructure.auth_users import fetch_user_by_id
from infrastructure.config import Settings
from infrastructure.expense_submit_mail import notify_expense_author_decision, notify_expense_author_paid

_log = logging.getLogger(__name__)
_TIMEOUT_SEC = 90.0


async def _run_author_decision_notification(
    settings: Settings,
    *,
    authorization: Optional[str],
    author_user_id: int,
    expense_id: str,
    decision: Literal["approved", "rejected"],
    reject_reason: str | None,
) -> None:
    if not settings.expense_notify_author_on_decision:
        return

    fb = (settings.expense_auth_bearer_for_author_email or "").strip() or None
    profile = await fetch_user_by_id(
        settings.auth_service_url,
        authorization,
        author_user_id,
        fallback_bearer=fb,
    )
    if not profile:
        _log.warning(
            "expense author notify: профиль автора не получен (auth) expense_id=%s user_id=%s",
            expense_id,
            author_user_id,
        )
        return

    email = profile.get("email")
    if not email or not str(email).strip():
        _log.warning(
            "expense author notify: у автора нет email expense_id=%s user_id=%s",
            expense_id,
            author_user_id,
        )
        return

    name = profile.get("display_name")
    await notify_expense_author_decision(
        settings,
        to_email=str(email).strip(),
        display_name=str(name).strip() if name else None,
        expense_id=expense_id,
        decision=decision,
        reject_reason=reject_reason,
    )


async def run_author_decision_notification_safe(
    settings: Settings,
    *,
    authorization: Optional[str],
    author_user_id: int,
    expense_id: str,
    decision: Literal["approved", "rejected"],
    reject_reason: str | None,
) -> None:

    try:
        await asyncio.wait_for(
            _run_author_decision_notification(
                settings,
                authorization=authorization,
                author_user_id=author_user_id,
                expense_id=expense_id,
                decision=decision,
                reject_reason=reject_reason,
            ),
            timeout=_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        _log.error(
            "expense author notify: timeout after %ss expense_id=%s",
            _TIMEOUT_SEC,
            expense_id,
        )
    except Exception:
        _log.exception("expense author notify failed expense_id=%s", expense_id)


def _format_paid_by_line(
    *,
    paid_by_user_id: int,
    paid_by_display_name: str | None,
    paid_by_email: str | None,
) -> str:
    name = (paid_by_display_name or "").strip()
    email = (paid_by_email or "").strip()
    if name and email:
        return f"{name} ({email})"
    if name:
        return name
    if email:
        return email
    return f"Пользователь #{paid_by_user_id}"


async def _run_expense_paid_notification(
    settings: Settings,
    *,
    authorization: Optional[str],
    author_user_id: int,
    expense_id: str,
    paid_by_user_id: int,
    paid_by_display_name: str | None,
    paid_by_email: str | None,
) -> None:
    if not settings.expense_notify_author_on_paid:
        return

    fb = (settings.expense_auth_bearer_for_author_email or "").strip() or None
    profile = await fetch_user_by_id(
        settings.auth_service_url,
        authorization,
        author_user_id,
        fallback_bearer=fb,
    )
    if not profile:
        _log.warning(
            "expense author paid notify: профиль автора не получен (auth) expense_id=%s user_id=%s",
            expense_id,
            author_user_id,
        )
        return

    email = profile.get("email")
    if not email or not str(email).strip():
        _log.warning(
            "expense author paid notify: у автора нет email expense_id=%s user_id=%s",
            expense_id,
            author_user_id,
        )
        return

    name = profile.get("display_name")
    paid_by_line = _format_paid_by_line(
        paid_by_user_id=paid_by_user_id,
        paid_by_display_name=paid_by_display_name,
        paid_by_email=paid_by_email,
    )
    await notify_expense_author_paid(
        settings,
        to_email=str(email).strip(),
        display_name=str(name).strip() if name else None,
        expense_id=expense_id,
        paid_by_line=paid_by_line,
    )


async def run_expense_paid_notification_safe(
    settings: Settings,
    *,
    authorization: Optional[str],
    author_user_id: int,
    expense_id: str,
    paid_by_user_id: int,
    paid_by_display_name: str | None,
    paid_by_email: str | None,
) -> None:

    try:
        await asyncio.wait_for(
            _run_expense_paid_notification(
                settings,
                authorization=authorization,
                author_user_id=author_user_id,
                expense_id=expense_id,
                paid_by_user_id=paid_by_user_id,
                paid_by_display_name=paid_by_display_name,
                paid_by_email=paid_by_email,
            ),
            timeout=_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        _log.error(
            "expense author paid notify: timeout after %ss expense_id=%s",
            _TIMEOUT_SEC,
            expense_id,
        )
    except Exception:
        _log.exception("expense author paid notify failed expense_id=%s", expense_id)
