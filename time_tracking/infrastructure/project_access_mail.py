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


def normalize_records_language(records_language: str | None) -> str:
    lang = (records_language or "ENG").strip().upper()
    return "RU" if lang == "RU" else "ENG"


def records_language_notice(records_language: str | None) -> str:
    if normalize_records_language(records_language) == "RU":
        return "Записи вносим на русском языке."
    return "Записи вносим на английском языке."


def records_language_label(records_language: str | None) -> str:
    if normalize_records_language(records_language) == "RU":
        return "Русский (RU)"
    return "Английский (ENG)"


def build_project_access_added_subject(*, project_name: str) -> str:
    name = (project_name or "").strip() or "проект"
    return f"Вас включили в проект — {name}"


def build_project_access_added_bodies(
    *,
    project_name: str,
    client_name: str,
    records_language: str | None = "ENG",
    signature_name: str,
    signature_title: str,
) -> tuple[str, str]:
    proj = (project_name or "").strip() or "—"
    client = (client_name or "").strip() or "—"
    sig_name = (signature_name or "").strip() or "Гузаль Темирова"
    sig_title = (signature_title or "").strip() or "Контрактный менеджер"
    lang_notice = records_language_notice(records_language)
    lang_label = records_language_label(records_language)

    text = (
        "Добрый день.\n\n"
        f"В нашей системе Вас включили в проект — {proj}, клиент — {client}.\n\n"
        f"{lang_notice}\n\n"
        "Благодарим за внимание.\n\n"
        f"С уважением,\n{sig_name}\n{sig_title}"
    )

    proj_h = html.escape(proj)
    client_h = html.escape(client)
    lang_notice_h = html.escape(lang_notice)
    lang_label_h = html.escape(lang_label)
    sig_name_h = html.escape(sig_name)
    sig_title_h = html.escape(sig_title)

    html_body = f"""<!DOCTYPE html>
<html lang="ru">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#e2e8f0;font-family:Segoe UI,Arial,sans-serif;color:#0f172a;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#e2e8f0;padding:28px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:600px;background:#ffffff;border-radius:14px;overflow:hidden;box-shadow:0 14px 32px rgba(15,23,42,.12);">
<tr>
  <td style="padding:22px 28px;background:linear-gradient(135deg,#1e3a8a 0%,#2563eb 100%);">
    <p style="margin:0;font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:rgba(255,255,255,.82);">Kosta Legal · Time Tracking</p>
    <h1 style="margin:8px 0 0 0;font-size:22px;font-weight:600;line-height:1.35;color:#ffffff;">Доступ к проекту</h1>
  </td>
</tr>
<tr>
  <td style="padding:28px 28px 8px 28px;font-size:15px;line-height:1.6;">
    <p style="margin:0 0 18px 0;">Добрый день.</p>
    <p style="margin:0 0 20px 0;">В нашей системе Вас включили в проект. Ниже — основные сведения:</p>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-collapse:collapse;background:#f8fafc;border-radius:10px;overflow:hidden;border:1px solid #e2e8f0;">
      <tr>
        <td style="padding:12px 16px;border-bottom:1px solid #e2e8f0;font-size:13px;color:#64748b;width:34%;">Проект</td>
        <td style="padding:12px 16px;border-bottom:1px solid #e2e8f0;font-size:15px;font-weight:600;color:#0f172a;">{proj_h}</td>
      </tr>
      <tr>
        <td style="padding:12px 16px;font-size:13px;color:#64748b;">Клиент</td>
        <td style="padding:12px 16px;font-size:15px;font-weight:600;color:#0f172a;">{client_h}</td>
      </tr>
    </table>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin-top:18px;border-collapse:collapse;background:#eff6ff;border-radius:10px;overflow:hidden;border:1px solid #bfdbfe;">
      <tr>
        <td style="padding:14px 16px;">
          <p style="margin:0 0 4px 0;font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:#1d4ed8;">Язык записей</p>
          <p style="margin:0;font-size:15px;font-weight:600;color:#1e3a8a;">{lang_label_h}</p>
          <p style="margin:8px 0 0 0;font-size:14px;line-height:1.5;color:#1e40af;">{lang_notice_h}</p>
        </td>
      </tr>
    </table>
    <p style="margin:22px 0 0 0;">Благодарим за внимание.</p>
  </td>
</tr>
<tr>
  <td style="padding:8px 28px 24px 28px;font-size:15px;line-height:1.55;color:#334155;">
    <p style="margin:0;">С уважением,<br><strong style="color:#0f172a;">{sig_name_h}</strong><br>{sig_title_h}</p>
  </td>
</tr>
<tr>
  <td style="padding:12px 20px;background:#f1f5f9;border-top:1px solid #e2e8f0;font-size:11px;line-height:1.45;color:#64748b;text-align:center;">
    Это автоматическое уведомление. Отвечать на это письмо не обязательно.
  </td>
</tr>
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
    records_language: str | None = "ENG",
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
        records_language=records_language,
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
