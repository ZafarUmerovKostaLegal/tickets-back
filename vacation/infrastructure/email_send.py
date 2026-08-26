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
from infrastructure.config import Settings, get_settings, is_managing_partner_email
from infrastructure.email_action_token import STAGE_FINAL, STAGE_PARTNER, sign_email_action_token
from infrastructure.models import LEAVE_STATUS_APPROVED, LeaveRequest

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


def _action_urls(
    settings: Settings,
    request_id: int,
    *,
    stage: str = STAGE_PARTNER,
) -> tuple[str | None, str | None]:
    if email_action_missing(settings):
        return None, None
    sec = settings.email_action_secret.strip()
    base_api = settings.public_api_base_url.strip().rstrip("/")
    try:
        ttl = int(settings.email_action_ttl_seconds)
        t_ap = sign_email_action_token(sec, request_id=request_id, action="approve", ttl_seconds=ttl, stage=stage)
        t_rj = sign_email_action_token(sec, request_id=request_id, action="decline", ttl_seconds=ttl, stage=stage)
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


def _build_html(
    req: LeaveRequest,
    approve_url: str | None,
    decline_url: str | None,
    open_link: str | None,
    *,
    eyebrow: str = "Согласование отсутствия",
    note: str | None = None,
) -> str:
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
    note_html = (
        f'<p style="margin:0 0 10px 0;padding:10px 12px;background:#eff6ff;border:1px solid #bfdbfe;'
        f'border-radius:10px;font-size:13px;color:#1e3a8a;">{html.escape(note)}</p>'
        if note
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
        <p style="margin:0 0 6px 0;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#64748b;">{html.escape(eyebrow)}</p>
        <h1 style="margin:0 0 12px 0;font-size:20px;color:#0f172a;">{html.escape(kind_ru)}</h1>
        {note_html}
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


def _plain_text(
    req: LeaveRequest,
    approve_url: str | None,
    decline_url: str | None,
    *,
    note: str | None = None,
) -> str:
    kind_ru = KIND_LABELS_RU.get(req.kind_code, "—")
    lines = [
        f"Заявка на отсутствие #{req.id}",
        *([note] if note else []),
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


async def _send_for_decision(
    req: LeaveRequest,
    pdf_bytes: bytes | None,
    *,
    to_email: str,
    stage: str,
    subject: str,
    eyebrow: str,
    note: str | None,
) -> bool:
    settings = get_settings()
    if not smtp_ready(settings):
        _log.warning(
            "vacation mail: SMTP не настроен (%s), request_id=%s",
            ", ".join(smtp_missing(settings)),
            req.id,
        )
        return False
    recipient = (to_email or "").strip()
    if not recipient:
        _log.warning("vacation mail: нет адресата для ступени %s, request_id=%s", stage, req.id)
        return False

    approve_url, decline_url = _action_urls(settings, req.id, stage=stage)
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

    html_body = _build_html(req, approve_url, decline_url, open_link, eyebrow=eyebrow, note=note)
    text_body = _plain_text(req, approve_url, decline_url, note=note)

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    from_addr = (settings.mail_from or settings.smtp_user or "").strip()
    if not from_addr:
        _log.warning("vacation mail: пустой отправитель — задайте VACATION_MAIL_FROM или VACATION_SMTP_USER")
        return False
    msg["From"] = from_addr
    to_list = [recipient]
    bcc_list = [x.strip() for x in (settings.mail_bcc or "").split(",") if x.strip()]
    msg["To"] = ", ".join(to_list)
    if bcc_list:
        msg["Bcc"] = ", ".join(bcc_list)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(text_body, "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    if pdf_bytes:
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
    _log.info("vacation mail sent: request_id=%s stage=%s to=%s", req.id, stage, to_list + bcc_list)
    return True


async def send_leave_request_to_partner(req: LeaveRequest, pdf_bytes: bytes) -> bool:
    """Первая ступень: заявка уходит курирующему партнёру, выбранному сотрудником."""
    direct_final = is_managing_partner_email(req.partner_email)
    return await _send_for_decision(
        req,
        pdf_bytes,
        to_email=req.partner_email or "",
        stage=STAGE_PARTNER,
        subject=f"Заявка на отсутствие #{req.id} — {KIND_LABELS_RU.get(req.kind_code, '—')}",
        eyebrow=(
            "Согласование отсутствия · управляющий партнёр"
            if direct_final
            else "Согласование отсутствия · курирующий партнёр"
        ),
        note=(
            "Вас выбрали курирующим партнёром. Вы же управляющий партнёр — "
            "ваше решение сразу финальное, после утверждения дни попадут в график."
            if direct_final
            else None
        ),
    )


async def send_leave_request_to_managing_partner(req: LeaveRequest, pdf_bytes: bytes | None) -> bool:
    """Вторая ступень: обязательное финальное подтверждение управляющего партнёра."""
    settings = get_settings()
    partner = (req.partner_full_name or "").strip() or "Курирующий партнёр"
    return await _send_for_decision(
        req,
        pdf_bytes,
        to_email=settings.managing_partner_email,
        stage=STAGE_FINAL,
        subject=f"Финальное согласование заявки #{req.id} — {KIND_LABELS_RU.get(req.kind_code, '—')}",
        eyebrow="Согласование отсутствия · управляющий партнёр",
        note=(
            f"{partner} согласовал заявку. Требуется ваше финальное подтверждение — "
            "только после него дни попадут в график отсутствий."
        ),
    )


async def send_partner_approval_to_employee(req: LeaveRequest) -> bool:
    """Сообщает сотруднику, что первая ступень пройдена и заявка ушла управляющему партнёру."""
    settings = get_settings()
    if not smtp_ready(settings) or not (req.employee_email or "").strip():
        return False
    kind_ru = KIND_LABELS_RU.get(req.kind_code, "—")
    partner = (req.partner_full_name or "").strip() or "Курирующий партнёр"
    managing = (settings.managing_partner_name or "").strip() or "управляющий партнёр"
    period_iso = f"{req.date_from.isoformat()} — {req.date_to.isoformat()}"
    period_ru = f"{req.date_from.strftime('%d.%m.%Y')} — {req.date_to.strftime('%d.%m.%Y')}"
    subject = f"Заявка #{req.id} согласована курирующим партнёром"
    text_body = (
        f"{partner} согласовал вашу заявку #{req.id} ({kind_ru}) на период {period_iso}.\n"
        f"Заявка отправлена на финальное подтверждение: {managing}.\n\n— Kosta Legal · vacation"
    )
    html_body = (
        f"<p><b>{html.escape(partner)}</b> согласовал вашу заявку <b>#{req.id}</b> "
        f"({html.escape(kind_ru)}) на период {period_ru}.</p>"
        f"<p>Заявка отправлена на финальное подтверждение: <b>{html.escape(managing)}</b>.</p>"
    )
    return await _send_simple(
        settings,
        to_email=req.employee_email,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        log_scope="stage mail",
        request_id=req.id,
    )


async def _send_simple(
    settings: Settings,
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str,
    log_scope: str,
    request_id: int,
) -> bool:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    from_addr = (settings.mail_from or settings.smtp_user or "").strip()
    if not from_addr:
        return False
    msg["From"] = from_addr
    msg["To"] = to_email.strip()
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
        _log.error("vacation %s: ошибка SMTP request_id=%s: %r", log_scope, request_id, exc)
        return False
    return True


async def send_cancellation_to_partner(
    req: LeaveRequest,
    *,
    before_decision: bool,
    also_managing_partner: bool = False,
) -> bool:
    """Сообщает согласующим, что сотрудник отозвал заявку или отменил согласованное отсутствие."""
    settings = get_settings()
    if not smtp_ready(settings):
        return False
    recipients = [x for x in ((req.partner_email or "").strip(),) if x]
    if also_managing_partner:
        managing_email = (settings.managing_partner_email or "").strip()
        if managing_email and managing_email.casefold() not in {x.casefold() for x in recipients}:
            recipients.append(managing_email)
    if not recipients:
        return False
    kind_ru = KIND_LABELS_RU.get(req.kind_code, "—")
    action_ru = "отозвал заявку" if before_decision else "отменил согласованное отсутствие"
    subject = (
        f"Заявка #{req.id} отозвана"
        if before_decision
        else f"Заявка #{req.id} отменена сотрудником"
    )
    period_iso = f"{req.date_from.isoformat()} — {req.date_to.isoformat()}"
    period_ru = f"{req.date_from.strftime('%d.%m.%Y')} — {req.date_to.strftime('%d.%m.%Y')}"
    schedule_note = (
        ""
        if before_decision
        else "\n\nДни этого отсутствия убраны из графика."
    )
    reason_block = f"\n\nКомментарий: {req.decision_reason}" if req.decision_reason else ""
    text_body = (
        f"{req.employee_full_name} {action_ru} #{req.id} ({kind_ru}) на период {period_iso}."
        f"{schedule_note}{reason_block}\n\n— Kosta Legal · vacation"
    )
    html_body = (
        f"<p><b>{html.escape(req.employee_full_name)}</b> {action_ru} "
        f"<b>#{req.id}</b> ({html.escape(kind_ru)}) на период {period_ru}.</p>"
        + ("" if before_decision else "<p>Дни этого отсутствия убраны из графика.</p>")
        + (
            f"<p>Комментарий: {html.escape(req.decision_reason or '')}</p>"
            if req.decision_reason
            else ""
        )
    )
    sent = False
    for recipient in recipients:
        ok = await _send_simple(
            settings,
            to_email=recipient,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            log_scope="cancel mail",
            request_id=req.id,
        )
        sent = sent or ok
    return sent


async def send_decision_to_employee(req: LeaveRequest) -> bool:
    settings = get_settings()
    if not smtp_ready(settings) or not (req.employee_email or "").strip():
        return False
    approved = req.status == LEAVE_STATUS_APPROVED
    decision_ru = "утверждена" if approved else "отклонена"
    # Отклонить могли на любой ступени: финальные поля заполнены только у второй.
    final_stage = req.final_decision_at is not None
    managing = (settings.managing_partner_name or "").strip() or "управляющий партнёр"
    partner = (req.partner_full_name or "").strip() or "курирующий партнёр"
    by_ru = managing if final_stage else partner
    reason = (req.final_decision_reason if final_stage else req.decision_reason) or ""
    subject = f"Заявка #{req.id} {decision_ru}"
    reason_block = f"\n\nКомментарий: {reason}" if reason else ""
    text_body = (
        f"Ваша заявка #{req.id} ({KIND_LABELS_RU.get(req.kind_code, '—')}) "
        f"на период {req.date_from.isoformat()} — {req.date_to.isoformat()} {decision_ru}: {by_ru}."
        f"{reason_block}\n\n— Kosta Legal · vacation"
    )
    html_body = (
        f"<p>Ваша заявка <b>#{req.id}</b> ({html.escape(KIND_LABELS_RU.get(req.kind_code, '—'))}) "
        f"на период {req.date_from.strftime('%d.%m.%Y')} — {req.date_to.strftime('%d.%m.%Y')} "
        f"<b>{decision_ru}</b>: {html.escape(by_ru)}.</p>"
        + (f"<p>Комментарий: {html.escape(reason)}</p>" if reason else "")
    )
    return await _send_simple(
        settings,
        to_email=req.employee_email,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        log_scope="decision mail",
        request_id=req.id,
    )
