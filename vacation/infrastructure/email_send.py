from __future__ import annotations

import html
import logging
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote

import aiosmtplib

from application.kind_legend import KIND_LABELS_RU
from infrastructure.config import Settings, get_settings
from infrastructure.email_action_token import sign_email_action_token
from infrastructure.models import LeaveRequest

_log = logging.getLogger("vacation.email_send")


def smtp_ready(settings: Settings) -> bool:
    return bool(
        (settings.smtp_host or "").strip()
        and (settings.smtp_user or "").strip()
        and (settings.smtp_password or "").strip()
    )


def smtp_missing(settings: Settings) -> list[str]:
    miss: list[str] = []
    if not (settings.smtp_host or "").strip():
        miss.append("VACATION_SMTP_HOST")
    if not (settings.smtp_user or "").strip():
        miss.append("VACATION_SMTP_USER")
    if not (settings.smtp_password or "").strip():
        miss.append("VACATION_SMTP_PASSWORD")
    return miss


def email_action_missing(settings: Settings) -> list[str]:
    miss: list[str] = []
    if not (settings.email_action_secret or "").strip():
        miss.append("VACATION_EMAIL_ACTION_SECRET")
    if not (settings.public_api_base_url or "").strip():
        miss.append("GATEWAY_BASE_URL")
    return miss


def email_action_ready(settings: Settings) -> bool:
    return not email_action_missing(settings)


def _action_urls(settings: Settings, request_id: int) -> tuple[str | None, str | None]:
    if email_action_missing(settings):
        return None, None
    sec = settings.email_action_secret.strip()
    base_api = settings.public_api_base_url.strip().rstrip("/")
    try:
        ttl = int(settings.email_action_ttl_seconds)
        t_ap = sign_email_action_token(sec, request_id=request_id, action="approve", ttl_seconds=ttl)
        t_rj = sign_email_action_token(sec, request_id=request_id, action="decline", ttl_seconds=ttl)
    except ValueError as exc:
        _log.warning("email action token failed: %s", exc)
        return None, None
    suffix = "&confirm=1" if settings.email_action_confirm_step else ""
    return (
        f"{base_api}/api/v1/vacations/leave-requests/email-action?token={quote(t_ap, safe='')}{suffix}",
        f"{base_api}/api/v1/vacations/leave-requests/email-action?token={quote(t_rj, safe='')}{suffix}",
    )


def _button_html(href: str, label: str, bg: str) -> str:
    safe_href = html.escape(href, quote=True)
    return (
        f'<table role="presentation" cellspacing="0" cellpadding="0" border="0" '
        f'style="margin:0 0 8px 0;width:100%;"><tr><td align="center" '
        f'style="border-radius:8px;background:{bg};">'
        f'<a href="{safe_href}" target="_blank" rel="noopener noreferrer" '
        f'style="display:block;padding:11px 16px;font-family:Segoe UI,Arial,sans-serif;'
        f'font-size:15px;color:#ffffff !important;text-decoration:none;font-weight:700;'
        f'border-radius:8px;">{html.escape(label)}</a></td></tr></table>'
    )


def _build_html(req: LeaveRequest, approve_url: str | None, decline_url: str | None, open_link: str | None) -> str:
    kind_ru = KIND_LABELS_RU.get(req.kind_code, "—")
    employee = html.escape(req.employee_full_name or "—")
    if req.employee_email:
        employee = f"{employee} ({html.escape(req.employee_email)})"
    period = f"с <b>{req.date_from.strftime('%d.%m.%Y')}</b> по <b>{req.date_to.strftime('%d.%m.%Y')}</b> · {req.days_count} дн."
    reason = (req.reason or "").strip()
    reason_html = (
        f'<p style="margin:8px 0 0 0;font-size:13px;color:#475569;">Комментарий сотрудника: '
        f'{html.escape(reason)}</p>'
        if reason
        else ""
    )
    actions = ""
    if approve_url and decline_url:
        actions = (
            f'<div style="padding:12px 14px;background:#eef2ff;border-radius:10px;border:1px solid #c7d2fe;">'
            f'<p style="margin:0 0 6px 0;font-family:Segoe UI,Arial,sans-serif;font-size:15px;font-weight:700;color:#1e1b4b;">Решение</p>'
            f'{_button_html(approve_url, "✓ Утвердить", "#16a34a")}'
            f'{_button_html(decline_url, "✕ Отклонить", "#dc2626")}'
            f'<p style="margin:6px 0 0 0;font-size:11px;color:#475569;">'
            f'PDF-заявка приложена к этому письму.</p></div>'
        )
    else:
        missing = email_action_missing(get_settings())
        missing_txt = ", ".join(f"<b>{name}</b>" for name in missing) if missing else (
            "<b>VACATION_EMAIL_ACTION_SECRET</b> и <b>GATEWAY_BASE_URL</b>"
        )
        actions = (
            f'<div style="padding:12px 14px;background:#fff7ed;border-radius:10px;border:1px solid #fdba74;">'
            f'<p style="margin:0;font-size:13px;color:#7c2d12;">Кнопки решения не активны: задайте '
            f'{missing_txt} в env сервиса vacation и перезапустите контейнер.</p>'
            f'</div>'
        )
    open_block = ""
    if open_link:
        open_block = (
            f'<p style="margin:12px 0 0 0;font-size:12px;">'
            f'<a href="{html.escape(open_link, quote=True)}" style="color:#2563eb;">Открыть заявку в приложении</a>'
            f'</p>'
        )
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"/></head>
<body style="margin:0;font-family:Segoe UI,Arial,sans-serif;background:#f1f5f9;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f1f5f9;padding:24px 12px;">
  <tr><td align="center">
    <table role="presentation" width="560" cellspacing="0" cellpadding="0" border="0" style="max-width:560px;background:#ffffff;border-radius:12px;border:1px solid #e2e8f0;padding:24px;">
      <tr><td>
        <p style="margin:0 0 6px 0;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#64748b;">Согласование отсутствия</p>
        <h1 style="margin:0 0 12px 0;font-size:20px;color:#0f172a;">{html.escape(kind_ru)}</h1>
        <p style="margin:0 0 6px 0;font-size:14px;color:#0f172a;"><b>Сотрудник:</b> {employee}</p>
        <p style="margin:0 0 6px 0;font-size:14px;color:#0f172a;"><b>Период:</b> {period}</p>
        {reason_html}
        <div style="margin:18px 0 0 0;">
          {actions}
        </div>
        {open_block}
        <p style="margin:18px 0 0 0;font-size:11px;color:#94a3b8;">Письмо отправлено сервисом Kosta Legal · vacation.</p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""


def _plain_text(req: LeaveRequest, approve_url: str | None, decline_url: str | None) -> str:
    kind_ru = KIND_LABELS_RU.get(req.kind_code, "—")
    lines = [
        f"Заявка на отсутствие #{req.id}",
        f"Сотрудник: {req.employee_full_name or '—'} <{req.employee_email or ''}>",
        f"Вид: {kind_ru}",
        f"Период: {req.date_from.isoformat()} — {req.date_to.isoformat()} ({req.days_count} дн.)",
    ]
    if req.reason:
        lines.append(f"Комментарий: {req.reason}")
    if approve_url and decline_url:
        lines.extend(["", "Утвердить:", approve_url, "", "Отклонить:", decline_url])
    lines.extend(["", "— Kosta Legal · vacation"])
    return "\n".join(lines)


async def send_leave_request_to_partner(req: LeaveRequest, pdf_bytes: bytes) -> bool:
    settings = get_settings()
    if not smtp_ready(settings):
        _log.warning(
            "vacation mail: SMTP не настроен (%s), request_id=%s",
            ", ".join(smtp_missing(settings)),
            req.id,
        )
        return False
    if not (req.partner_email or "").strip():
        _log.warning("vacation mail: у партнёра #%s нет email", req.partner_user_id)
        return False

    approve_url, decline_url = _action_urls(settings, req.id)
    if not approve_url or not decline_url:
        missing = email_action_missing(settings)
        _log.warning(
            "vacation mail: кнопки решения отключены (%s), request_id=%s",
            ", ".join(missing) if missing else "unknown",
            req.id,
        )
    open_link = None
    base_front = (settings.frontend_url or "").strip().rstrip("/")
    if base_front:
        open_link = f"{base_front}/vacations/requests/{req.id}"

    subject = f"Заявка на отсутствие #{req.id} — {KIND_LABELS_RU.get(req.kind_code, '—')}"
    html_body = _build_html(req, approve_url, decline_url, open_link)
    text_body = _plain_text(req, approve_url, decline_url)

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    from_addr = (settings.mail_from or settings.smtp_user or "").strip()
    if not from_addr:
        _log.warning("vacation mail: пустой отправитель — задайте VACATION_MAIL_FROM или VACATION_SMTP_USER")
        return False
    msg["From"] = from_addr
    to_list = [req.partner_email.strip()]
    bcc_list = [x.strip() for x in (settings.mail_bcc or "").split(",") if x.strip()]
    msg["To"] = ", ".join(to_list)
    if bcc_list:
        msg["Bcc"] = ", ".join(bcc_list)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(text_body, "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    pdf_part = MIMEBase("application", "pdf")
    pdf_part.set_payload(pdf_bytes)
    encoders.encode_base64(pdf_part)
    pdf_part.add_header(
        "Content-Disposition",
        "attachment",
        filename=f"leave_request_{req.id}.pdf",
    )
    msg.attach(pdf_part)

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host.strip(),
            port=int(settings.smtp_port),
            username=settings.smtp_user.strip(),
            password=settings.smtp_password,
            start_tls=bool(settings.smtp_use_tls),
            recipients=to_list + bcc_list,
        )
    except Exception as exc:
        _log.error("vacation mail: ошибка SMTP request_id=%s: %r", req.id, exc)
        return False
    _log.info("vacation mail sent: request_id=%s to=%s", req.id, to_list + bcc_list)
    return True


async def send_decision_to_employee(req: LeaveRequest) -> bool:
    settings = get_settings()
    if not smtp_ready(settings) or not (req.employee_email or "").strip():
        return False
    decision_ru = "утверждена" if req.status == "approved" else "отклонена"
    subject = f"Заявка #{req.id} {decision_ru}"
    reason_block = ""
    if req.decision_reason:
        reason_block = f"\n\nКомментарий: {req.decision_reason}"
    text_body = (
        f"Ваша заявка #{req.id} ({KIND_LABELS_RU.get(req.kind_code, '—')}) "
        f"на период {req.date_from.isoformat()} — {req.date_to.isoformat()} {decision_ru}."
        f"{reason_block}\n\n— Kosta Legal · vacation"
    )
    html_body = (
        f"<p>Ваша заявка <b>#{req.id}</b> ({html.escape(KIND_LABELS_RU.get(req.kind_code, '—'))}) "
        f"на период {req.date_from.strftime('%d.%m.%Y')} — {req.date_to.strftime('%d.%m.%Y')} "
        f"<b>{decision_ru}</b>.</p>"
        + (f"<p>Комментарий: {html.escape(req.decision_reason or '')}</p>" if req.decision_reason else "")
    )
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    from_addr = (settings.mail_from or settings.smtp_user or "").strip()
    if not from_addr:
        return False
    msg["From"] = from_addr
    msg["To"] = req.employee_email.strip()
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host.strip(),
            port=int(settings.smtp_port),
            username=settings.smtp_user.strip(),
            password=settings.smtp_password,
            start_tls=bool(settings.smtp_use_tls),
        )
    except Exception as exc:
        _log.error("vacation decision mail: ошибка SMTP request_id=%s: %r", req.id, exc)
        return False
    return True
