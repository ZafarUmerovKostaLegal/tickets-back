from __future__ import annotations

import asyncio
import html
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Literal, Optional

import aiosmtplib

from infrastructure.auth_users import fetch_user_by_id
from infrastructure.config import Settings

_log = logging.getLogger(__name__)
_TIMEOUT_SEC = 90.0

CorrMailKind = Literal["review", "approved", "rejected", "signed", "incoming"]


def smtp_ready(settings: Settings) -> bool:
    return bool(
        (settings.smtp_host or "").strip()
        and (settings.smtp_user or "").strip()
        and (settings.smtp_password or "").strip()
    )


def smtp_status_summary(settings: Settings) -> str:
    host = (settings.smtp_host or "").strip()
    user = (settings.smtp_user or "").strip()
    pwd = bool((settings.smtp_password or "").strip())
    mail_from = (settings.mail_from or settings.smtp_user or "").strip()
    return (
        f"host={'set' if host else 'missing'}({host or '-'}) "
        f"user={'set' if user else 'missing'} "
        f"password={'set' if pwd else 'missing'} "
        f"from={'set' if mail_from else 'missing'} "
        f"port={settings.smtp_port} tls={bool(settings.smtp_use_tls)}"
    )


def _app_link(settings: Settings, *, kind: CorrMailKind = "review") -> str | None:
    base = (settings.public_app_url or "").strip().rstrip("/")
    if not base:
        return None
    if kind == "incoming":
        return f"{base}/correspondence?tab=incoming&view=attention"
    return f"{base}/correspondence?tab=outgoing"


def _build_message(
    *,
    kind: CorrMailKind,
    subject_line: str,
    counterparty: str,
    registry_number: str | None,
    reject_comment: str | None,
    open_url: str | None,
) -> tuple[str, str, str]:
    title = {
        "review": "Исходящее письмо на согласовании",
        "approved": "Письмо одобрено — загрузите подписанный скан",
        "rejected": "Исходящее письмо отклонено",
        "signed": "Подписанный скан загружен",
        "incoming": "Новое входящее письмо",
    }[kind]
    reg = (registry_number or "").strip()
    body_lines = [
        f"Тема: {subject_line}",
        f"Контрагент: {counterparty}",
    ]
    if reg:
        body_lines.append(f"Реестровый номер: {reg}")
    if kind == "approved":
        body_lines.extend(
            [
                "",
                "Следующий шаг:",
                "1) Распечатайте письмо",
                "2) Поставьте подпись",
                "3) Загрузите подписанный скан в карточку документа",
            ]
        )
    if kind == "incoming":
        body_lines.extend(
            [
                "",
                "В реестр корреспонденции добавлено входящее письмо, назначенное вам.",
                "Откройте раздел «Корреспонденция» → «Входящие» → «Нужно посмотреть».",
            ]
        )
    if kind == "rejected" and (reject_comment or "").strip():
        body_lines.extend(["", f"Комментарий: {reject_comment.strip()}"])
    if open_url:
        body_lines.extend(["", f"Открыть: {open_url}"])

    text = f"{title}\n\n" + "\n".join(body_lines)
    rows = "".join(
        f"<p style='margin:0 0 8px 0;'>{html.escape(line) if line else '&nbsp;'}</p>"
        for line in body_lines
    )
    link_html = ""
    if open_url:
        safe = html.escape(open_url, quote=True)
        link_html = (
            f"<p style='margin:16px 0 0 0;'><a href=\"{safe}\" "
            f"style='display:inline-block;padding:10px 16px;background:#8b2635;color:#fff;"
            f"text-decoration:none;border-radius:8px;font-weight:600;'>Открыть в системе</a></p>"
        )
    html_body = (
        "<div style='font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1a1414;line-height:1.45;'>"
        f"<h2 style='margin:0 0 12px 0;font-size:18px;'>{html.escape(title)}</h2>"
        f"{rows}{link_html}"
        "</div>"
    )
    mail_subject = {
        "review": f"На согласовании: {subject_line}",
        "approved": f"Одобрено — загрузите подпись: {reg or subject_line}",
        "rejected": f"Отклонено: {subject_line}",
        "signed": f"Подписанный скан: {reg or subject_line}",
        "incoming": f"Новое входящее: {reg or subject_line}",
    }[kind]
    return mail_subject, text, html_body


async def _send_smtp(
    settings: Settings,
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str,
) -> None:
    if not smtp_ready(settings):
        _log.error(
            "correspondence mail skipped: SMTP not configured (%s)",
            smtp_status_summary(settings),
        )
        return
    from_addr = (settings.mail_from or settings.smtp_user or "").strip()
    if not from_addr:
        _log.error(
            "correspondence mail skipped: empty From (%s)",
            smtp_status_summary(settings),
        )
        return
    msg = MIMEMultipart("alternative")
    msg["From"] = from_addr
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    host = settings.smtp_host.strip()
    port = int(settings.smtp_port)
    use_tls = bool(settings.smtp_use_tls)
    # Port 465 = implicit TLS; 587 = STARTTLS (same as expenses).
    if port == 465:
        await aiosmtplib.send(
            msg,
            hostname=host,
            port=port,
            username=settings.smtp_user.strip(),
            password=settings.smtp_password,
            use_tls=True,
            start_tls=False,
        )
    else:
        await aiosmtplib.send(
            msg,
            hostname=host,
            port=port,
            username=settings.smtp_user.strip(),
            password=settings.smtp_password,
            start_tls=use_tls,
        )
    _log.info(
        "correspondence mail sent to=%s subject=%s host=%s port=%s",
        to_email,
        subject[:120],
        host,
        port,
    )


async def _send_to_user(
    settings: Settings,
    *,
    authorization: Optional[str],
    user_id: int,
    kind: CorrMailKind,
    subject_line: str,
    counterparty: str,
    registry_number: str | None = None,
    reject_comment: str | None = None,
) -> None:
    if not (authorization or "").strip():
        _log.error(
            "correspondence mail: no Authorization to resolve recipient email user_id=%s kind=%s",
            user_id,
            kind,
        )
        return
    profile = await fetch_user_by_id(settings.auth_service_url, authorization, user_id)
    if not profile:
        _log.error(
            "correspondence mail: no profile user_id=%s kind=%s auth=%s",
            user_id,
            kind,
            settings.auth_service_url,
        )
        return
    email = (profile.get("email") or "").strip()
    if not email:
        _log.error("correspondence mail: no email user_id=%s kind=%s", user_id, kind)
        return
    mail_subject, text_body, html_body = _build_message(
        kind=kind,
        subject_line=subject_line,
        counterparty=counterparty,
        registry_number=registry_number,
        reject_comment=reject_comment,
        open_url=_app_link(settings, kind=kind),
    )
    await _send_smtp(
        settings,
        to_email=email,
        subject=mail_subject,
        text_body=text_body,
        html_body=html_body,
    )


async def notify_correspondence_mail_safe(
    settings: Settings,
    *,
    authorization: Optional[str],
    recipient_user_id: int,
    kind: CorrMailKind,
    subject_line: str,
    counterparty: str,
    registry_number: str | None = None,
    reject_comment: str | None = None,
) -> None:
    try:
        await asyncio.wait_for(
            _send_to_user(
                settings,
                authorization=authorization,
                user_id=recipient_user_id,
                kind=kind,
                subject_line=subject_line,
                counterparty=counterparty,
                registry_number=registry_number,
                reject_comment=reject_comment,
            ),
            timeout=_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        _log.error("correspondence mail timeout kind=%s user_id=%s", kind, recipient_user_id)
    except Exception:
        _log.exception(
            "correspondence mail failed kind=%s user_id=%s (%s)",
            kind,
            recipient_user_id,
            smtp_status_summary(settings),
        )
