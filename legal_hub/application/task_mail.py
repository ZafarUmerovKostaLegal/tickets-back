from __future__ import annotations

import html
import logging
from email.message import EmailMessage

import aiosmtplib
import httpx

from infrastructure.config import Settings

_log = logging.getLogger("legal_hub.mail")


def smtp_ready(settings: Settings) -> bool:
    return bool((settings.smtp_host or "").strip() and (settings.smtp_user or "").strip() and (settings.smtp_password or "").strip())


def _display(user: dict) -> str:
    for key in ("display_name", "name", "email"):
        value = user.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(user.get("id", ""))


async def _profile(settings: Settings, authorization: str, user_id: int) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{settings.auth_service_url.rstrip('/')}/users/{user_id}/public",
            headers={"Authorization": authorization},
        )
    if response.status_code >= 400:
        return {}
    data = response.json()
    return data if isinstance(data, dict) else {}


async def _send(settings: Settings, *, to: str, subject: str, text: str) -> None:
    message = EmailMessage()
    message["From"] = (settings.mail_from or settings.smtp_user).strip()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    message.add_alternative(f"<p>{html.escape(text).replace(chr(10), '<br>')}</p>", subtype="html")
    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host.strip(),
        port=int(settings.smtp_port),
        username=settings.smtp_user.strip(),
        password=settings.smtp_password,
        start_tls=bool(settings.smtp_use_tls),
    )


async def notify_assignee(
    settings: Settings,
    *,
    authorization: str,
    assignee_user_id: int,
    actor: dict,
    title: str,
    project: str,
    due_on: str | None,
    kind: str,
) -> None:
    if not smtp_ready(settings) or not authorization or assignee_user_id <= 0:
        return
    if int(actor.get("id") or 0) == assignee_user_id:
        return
    try:
        profile = await _profile(settings, authorization, assignee_user_id)
        email = str(profile.get("email") or "").strip()
        if not email or "@" not in email:
            return
        link = f"{settings.public_app_url.rstrip('/')}/legal-hub"
        who = _display(actor)
        when = f"\nСрок: {due_on}" if due_on else ""
        matter = f"\nПроект: {project}" if project else ""
        if kind == "comment":
            subject = f"Комментарий к задаче: {title}"
            text = f"{who} написал в задаче «{title}».{matter}\n\n{link}"
        else:
            subject = f"Новая задача: {title}"
            text = f"{who} назначил вам задачу «{title}».{matter}{when}\n\n{link}"
        await _send(settings, to=email, subject=subject, text=text)
    except Exception:
        _log.exception("legal hub mail was not sent")
