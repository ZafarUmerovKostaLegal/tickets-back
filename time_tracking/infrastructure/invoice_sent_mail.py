"""Email the legal invoice page PDF to accounting after a client send."""

from __future__ import annotations

import base64
import html
import logging
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from infrastructure.config import Settings
from infrastructure.project_access_mail import smtp_missing_env_names, smtp_ready

_log = logging.getLogger(__name__)


def _parse_recipients(raw: str) -> list[str]:
    out: list[str] = []
    for part in (raw or "").replace(";", ",").split(","):
        addr = part.strip()
        if addr and "@" in addr and addr not in out:
            out.append(addr)
    return out


def _decode_pdf_base64(pdf_base64: str) -> bytes:
    raw = (pdf_base64 or "").strip()
    if not raw:
        raise ValueError("pdfBase64 is required")
    if "," in raw and raw.lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw, validate=False)
    except Exception as e:
        raise ValueError("pdfBase64 is not valid base64") from e
    if not data:
        raise ValueError("pdfBase64 decoded to empty payload")
    if len(data) > 8_000_000:
        raise ValueError("PDF attachment is too large (max 8 MB)")
    return data


async def send_invoice_last_page_to_accounting(
    settings: Settings,
    *,
    invoice_number: str,
    client_name: str | None = None,
    pdf_base64: str,
    pdf_file_name: str | None = None,
) -> dict[str, object]:
    """
    Send the last invoice page (legal invoice PDF) to accounting.
    Returns { sent: bool, recipients: list[str], skippedReason?: str }.
    """
    if not settings.notify_invoice_sent_accounting:
        return {"sent": False, "recipients": [], "skippedReason": "disabled"}

    recipients = _parse_recipients(settings.invoice_sent_notify_to)
    if not recipients:
        return {"sent": False, "recipients": [], "skippedReason": "no_recipients"}

    if not smtp_ready(settings):
        _log.warning(
            "invoice sent mail: SMTP не настроен (%s) — письмо не отправлено invoice=%s",
            ", ".join(smtp_missing_env_names(settings)),
            invoice_number,
        )
        return {
            "sent": False,
            "recipients": recipients,
            "skippedReason": "smtp_not_configured",
        }

    from_addr = (settings.mail_from or settings.smtp_user or "").strip()
    if not from_addr:
        _log.warning("invoice sent mail: пустой отправитель (TT_MAIL_FROM / TT_SMTP_USER)")
        return {"sent": False, "recipients": recipients, "skippedReason": "no_from"}

    pdf_bytes = _decode_pdf_base64(pdf_base64)
    inv = (invoice_number or "").strip() or "invoice"
    client = (client_name or "").strip() or "—"
    fname = (pdf_file_name or "").strip() or f"{inv}-invoice-page.pdf"
    if not fname.lower().endswith(".pdf"):
        fname = f"{fname}.pdf"

    subject = f"Счёт отправлен клиенту — {inv}"
    text_body = (
        "Добрый день.\n\n"
        f"Клиенту отправлен счёт {inv}.\n"
        f"Клиент: {client}.\n\n"
        "Во вложении — последняя страница счёта (invoice) в PDF.\n"
    )
    html_body = (
        "<p>Добрый день.</p>"
        f"<p>Клиенту отправлен счёт <strong>{html.escape(inv)}</strong>.</p>"
        f"<p>Клиент: {html.escape(client)}.</p>"
        "<p>Во вложении — последняя страница счёта (invoice) в PDF.</p>"
    )

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(text_body, "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    part = MIMEBase("application", "pdf")
    part.set_payload(pdf_bytes)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=fname)
    msg.attach(part)

    _log.info(
        "invoice sent mail: отправка to=%s invoice=%s host=%s",
        recipients,
        inv,
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
            "invoice sent mail: ошибка SMTP to=%s invoice=%s: %s: %s",
            recipients,
            inv,
            type(e).__name__,
            e,
        )
        raise

    return {"sent": True, "recipients": recipients}
