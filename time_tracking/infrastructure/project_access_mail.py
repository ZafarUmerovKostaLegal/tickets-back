from __future__ import annotations

import html
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from infrastructure.config import Settings

_log = logging.getLogger(__name__)


def smtp_ready(settings: Settings) -> bool:
    return bool(
        (settings.smtp_host or "").strip()
        and (settings.smtp_user or "").strip()
        and (settings.smtp_password or "").strip()
    )


def smtp_missing_env_names(settings: Settings) -> list[str]:
    out: list[str] = []
    if not (settings.smtp_host or "").strip():
        out.append("TT_SMTP_HOST (или EXPENSE_SMTP_HOST)")
    if not (settings.smtp_user or "").strip():
        out.append("TT_SMTP_USER (или EXPENSE_SMTP_USER)")
    if not (settings.smtp_password or "").strip():
        out.append("TT_SMTP_PASSWORD (или EXPENSE_SMTP_PASSWORD)")
    return out


def build_project_access_added_subject(*, project_name: str) -> str:
    name = (project_name or "").strip() or "проект"
    return f"Вас включили в проект — {name}"


def build_project_access_added_bodies(
    *,
    project_name: str,
    client_name: str,
    signature_name: str,
    signature_title: str,
) -> tuple[str, str]:
    proj = (project_name or "").strip() or "—"
    client = (client_name or "").strip() or "—"
    sig_name = (signature_name or "").strip() or "Гузаль Темирова"
    sig_title = (signature_title or "").strip() or "Контрактный менеджер"

    text = (
        "Добрый день.\n\n"
        f"В нашей системе Вас включили в проект — {proj}, клиент — {client}.\n\n"
        "Благодарим за внимание.\n\n"
        f"С уважением,\n{sig_name}\n{sig_title}"
    )

    html_body = f"""<!DOCTYPE html>
<html lang="ru"><body style="margin:0;padding:0;background:#f8fafc;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f8fafc;padding:24px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;">
<tr><td style="padding:28px 32px;font-family:Segoe UI,Arial,sans-serif;font-size:15px;line-height:1.55;color:#0f172a;">
<p style="margin:0 0 16px 0;">Добрый день.</p>
<p style="margin:0 0 16px 0;">В нашей системе Вас включили в проект — <strong>{html.escape(proj)}</strong>, клиент — <strong>{html.escape(client)}</strong>.</p>
<p style="margin:0 0 16px 0;">Благодарим за внимание.</p>
<p style="margin:24px 0 0 0;">С уважением,<br>{html.escape(sig_name)}<br>{html.escape(sig_title)}</p>
</td></tr>
</table>
</td></tr>
</table>
</body></html>"""

    return text, html_body


async def send_project_access_added_email(
    settings: Settings,
    *,
    to_email: str,
    project_name: str,
    client_name: str,
) -> None:
    if not settings.notify_project_access_added:
        return
    if not smtp_ready(settings):
        _log.warning(
            "project access mail: SMTP не настроен (%s) — письмо не отправлено to=%s project=%s",
            ", ".join(smtp_missing_env_names(settings)),
            to_email,
            project_name,
        )
        return

    recipient = (to_email or "").strip()
    if not recipient:
        return

    from_addr = (settings.mail_from or settings.smtp_user or "").strip()
    if not from_addr:
        _log.warning("project access mail: пустой отправитель (TT_MAIL_FROM / TT_SMTP_USER)")
        return

    subject = build_project_access_added_subject(project_name=project_name)
    text_body, html_body = build_project_access_added_bodies(
        project_name=project_name,
        client_name=client_name,
        signature_name=settings.project_access_mail_signature_name,
        signature_title=settings.project_access_mail_signature_title,
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = recipient
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    _log.info(
        "project access mail: отправка to=%s project=%s host=%s",
        recipient,
        project_name,
        settings.smtp_host.strip(),
    )
    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host.strip(),
            port=int(settings.smtp_port),
            username=settings.smtp_user.strip(),
            password=settings.smtp_password,
            start_tls=bool(settings.smtp_use_tls),
        )
    except Exception as e:
        _log.error(
            "project access mail: ошибка SMTP to=%s project=%s: %s: %s",
            recipient,
            project_name,
            type(e).__name__,
            e,
        )
        raise
