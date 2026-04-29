

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from email import encoders
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote, urlparse, urlunparse, parse_qs, urlencode

import aiosmtplib

from infrastructure.expense_notify_routing import resolve_expense_notify_recipients

if TYPE_CHECKING:
    from infrastructure.config import Settings

_log = logging.getLogger(__name__)

_INLINE_IMAGE_MAX_BYTES = 2 * 1024 * 1024
_INLINE_IMAGE_MAX = 5


def _is_inline_image_mime(mt: str) -> bool:
    m = (mt or "").strip().lower()
    if m == "image/jpg":
        m = "image/jpeg"
    return m in {"image/jpeg", "image/png", "image/gif", "image/webp"}


@dataclass
class AttachmentEmailItem:
    id: str
    file_name: str
    storage_key: str
    mime_type: str | None
    size_bytes: int
    attachment_kind: str | None


@dataclass
class ExpenseModerationEmailContext:
    expense_id: str
    description: str | None
    expense_date: date | datetime | None
    payment_deadline: date | None
    amount_uzs: Decimal | None
    exchange_rate: Decimal | None
    equivalent_amount: Decimal | None
    expense_type: str | None
    expense_subtype: str | None
    is_reimbursable: bool
    payment_method: str | None
    department_id: str | None
    project_id: str | None
    vendor: str | None
    business_purpose: str | None
    comment: str | None
    author_email: str | None
    author_name: str | None
    attachments: list[AttachmentEmailItem]


def append_url_intent(url: str, param: str, value: str) -> str:

    url = (url or "").strip()
    if not url:
        return url
    u = urlparse(url)
    frag = u.fragment or ""
    if frag:
        sep = "&" if "?" in frag else "?"
        new_frag = f"{frag}{sep}{param}={value}"
        return urlunparse((u.scheme, u.netloc, u.path, u.params, u.query, new_frag))
    qs = parse_qs(u.query, keep_blank_values=True)
    qs[param] = [value]
    new_q = urlencode(qs, doseq=True)
    return urlunparse((u.scheme, u.netloc, u.path, u.params, new_q, u.fragment))


def _smtp_ready(settings: Settings) -> bool:
    return bool(
        (settings.smtp_host or "").strip()
        and (settings.smtp_user or "").strip()
        and (settings.smtp_password or "").strip()
    )


def _smtp_missing_env_names(settings: Settings) -> list[str]:
    out: list[str] = []
    if not (settings.smtp_host or "").strip():
        out.append("EXPENSE_SMTP_HOST")
    if not (settings.smtp_user or "").strip():
        out.append("EXPENSE_SMTP_USER")
    if not (settings.smtp_password or "").strip():
        out.append("EXPENSE_SMTP_PASSWORD")
    return out


def _parse_recipients(raw: str) -> list[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def _format_date(d: date | datetime | None) -> str:
    if d is None:
        return "—"
    if isinstance(d, datetime):
        return d.date().isoformat()
    return d.isoformat()


def _format_money(amount: Decimal | None) -> str:
    if amount is None:
        return "—"
    return f"{amount.quantize(Decimal('0.01')):,.2f}".replace(",", " ")


def _format_rate(r: Decimal | None) -> str:
    if r is None:
        return "—"
    return f"{r.quantize(Decimal('0.000001')):,.6f}".replace(",", " ")


def _kind_label(k: str | None) -> str:
    if not k or not str(k).strip():
        return "—"
    m = {"payment_document": "Документ для оплаты", "payment_receipt": "Квитанция об оплате"}
    return m.get(str(k).strip(), str(k).strip())


def _build_open_link(settings: Settings, expense_id: str) -> str | None:
    base = (settings.frontend_url or "").strip().rstrip("/")
    if not base:
        return None
    try:
        return settings.expense_notify_link_template.format(
            frontend_url=base,
            expense_id=expense_id,
        )
    except (KeyError, ValueError) as e:
        _log.warning("EXPENSE_NOTIFY_LINK_TEMPLATE invalid: %s", e)
        return f"{base}/expenses/{expense_id}"


def _button_row_html(href: str, label: str, bg: str) -> str:
    safe_href = html.escape(href, quote=True)
    safe_label = html.escape(label)
    return f"""
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin:0 0 8px 0;width:100%;">
  <tr>
    <td align="center" style="border-radius:8px;background:{bg};">
      <a href="{safe_href}" target="_blank" rel="noopener noreferrer"
         style="display:block;padding:11px 16px;font-family:Segoe UI,Arial,sans-serif;font-size:15px;color:#ffffff !important;text-decoration:none;font-weight:700;border-radius:8px;">
        {safe_label}
      </a>
    </td>
  </tr>
</table>"""


def _detail_row(label: str, value_html: str) -> str:
    return f"""
<tr>
  <td style="padding:5px 10px;border-bottom:1px solid #e2e8f0;color:#64748b;font-size:13px;width:32%;vertical-align:top;">{html.escape(label)}</td>
  <td style="padding:5px 10px;border-bottom:1px solid #e2e8f0;color:#0f172a;font-size:13px;vertical-align:top;">{value_html}</td>
</tr>"""


def _attachment_item_html(
    *,
    kind_ru: str,
    safe_filename: str,
    sz_kb: int,
    href: str | None,
    preview_html: str = "",
) -> str:

    kind_esc = html.escape(kind_ru)
    size_esc = html.escape(f"~{sz_kb} КБ")
    row_tbl = f"""<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-collapse:collapse;">
  <tr>
    <td style="padding:0;vertical-align:middle;">
      <span style="font-size:15px;line-height:1;vertical-align:middle;">&#128196;</span>
      <span style="margin-left:8px;font-size:14px;font-weight:600;color:#0f172a;vertical-align:middle;">{safe_filename}</span>
    </td>
    <td align="right" style="padding:0 0 0 12px;vertical-align:middle;white-space:nowrap;font-size:12px;color:#64748b;">{size_esc}</td>
  </tr>
</table>"""
    box = (
        "box-sizing:border-box;padding:10px 12px;background:#ffffff;"
        "border:1px solid #e2e8f0;border-radius:8px;"
    )
    if href:
        su = html.escape(href, quote=True)
        row_block = (
            f'<a href="{su}" target="_blank" rel="noopener noreferrer" '
            f'style="display:block;{box}text-decoration:none;color:inherit;">'
            f"{row_tbl}</a>"
        )
    else:
        row_block = f'<div style="{box}">{row_tbl}</div>'
    prev = f'<div style="margin-top:8px;">{preview_html}</div>' if preview_html else ""
    return (
        f'<div style="margin:0 0 10px 0;">'
        f'<p style="margin:0 0 6px 0;font-size:12px;font-weight:700;color:#334155;">{kind_esc}</p>'
        f"{row_block}{prev}"
        f"</div>"
    )


def _build_moderation_html(
    *,
    ctx: ExpenseModerationEmailContext,
    safe_author: str,
    expense_date_fmt: str,
    money_fmt: str,
    reimb: str,
    safe_desc: str,
    safe_et: str,
    safe_sub: str,
    safe_vendor: str,
    safe_bp: str,
    safe_comment: str,
    dept: str,
    proj: str,
    pm: str,
    pd_fmt: str,
    rate_fmt: str,
    eq_fmt: str,
    open_link: str | None,
    actions_block: str,
    attachments_block: str,
) -> str:
    safe_id = html.escape(ctx.expense_id)
    footer_link_html = ""
    if open_link:
        footer_link_html = (
            f'<p style="margin:0 0 6px 0;font-size:11px;"><a href="{html.escape(open_link, quote=True)}" '
            f'style="color:#2563eb;">Открыть заявку в веб-приложении</a> (по желанию).</p>'
        )
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Заявка {safe_id}</title>
</head>
<body style="margin:0;padding:0;background:#e2e8f0;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;background:linear-gradient(180deg,#1e3a8a 0%,#e2e8f0 180px);">
  <tr>
    <td align="center" style="padding:10px 8px 20px 8px;">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 12px 28px rgba(15,23,42,.1);">
        <tr>
          <td style="padding:14px 16px 12px 16px;background:linear-gradient(135deg,#1d4ed8 0%,#2563eb 50%,#3b82f6 100%);color:#fff;font-family:Segoe UI,Arial,sans-serif;">
            <p style="margin:0 0 4px 0;font-size:11px;letter-spacing:.1em;text-transform:uppercase;opacity:.9;">Согласование расхода</p>
            <h1 style="margin:0;font-size:22px;font-weight:700;letter-spacing:-.02em;line-height:1.15;">{safe_id}</h1>
            <p style="margin:8px 0 0 0;font-size:14px;opacity:.95;line-height:1.4;">Нужно решение модератора. Ниже — полные данные заявки и вложения.</p>
          </td>
        </tr>
        <tr>
          <td style="padding:8px 14px 4px 14px;font-family:Segoe UI,Arial,sans-serif;">
            <p style="margin:0 0 6px 0;font-size:11px;font-weight:700;color:#0f172a;text-transform:uppercase;letter-spacing:.05em;">Автор и сроки</p>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-collapse:collapse;background:#f8fafc;border-radius:8px;overflow:hidden;">
              {_detail_row("Автор", safe_author)}
              {_detail_row("Дата расхода", html.escape(expense_date_fmt))}
              {_detail_row("Срок оплаты", html.escape(pd_fmt))}
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:4px 14px 4px 14px;font-family:Segoe UI,Arial,sans-serif;">
            <p style="margin:0 0 6px 0;font-size:11px;font-weight:700;color:#0f172a;text-transform:uppercase;letter-spacing:.05em;">Суммы</p>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-collapse:collapse;background:#f1f5f9;border-radius:8px;overflow:hidden;">
              {_detail_row("Сумма (UZS)", html.escape(money_fmt))}
              {_detail_row("Курс", html.escape(rate_fmt))}
              {_detail_row("Эквивалент", html.escape(eq_fmt))}
              {_detail_row("Возмещаемый", html.escape(reimb))}
              {_detail_row("Способ оплаты", html.escape(pm))}
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:4px 14px 4px 14px;font-family:Segoe UI,Arial,sans-serif;">
            <p style="margin:0 0 6px 0;font-size:11px;font-weight:700;color:#0f172a;text-transform:uppercase;letter-spacing:.05em;">Классификация</p>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-collapse:collapse;background:#f8fafc;border-radius:8px;overflow:hidden;">
              {_detail_row("Тип", safe_et)}
              {_detail_row("Подтип", safe_sub)}
              {_detail_row("Подразделение", html.escape(dept))}
              {_detail_row("Проект", html.escape(proj))}
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:4px 14px 4px 14px;font-family:Segoe UI,Arial,sans-serif;">
            <p style="margin:0 0 6px 0;font-size:11px;font-weight:700;color:#0f172a;text-transform:uppercase;letter-spacing:.05em;">Содержание</p>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border-collapse:collapse;background:#fffbeb;border-radius:8px;overflow:hidden;border:1px solid #fde68a;">
              {_detail_row("Описание", safe_desc)}
              {_detail_row("Контрагент", safe_vendor)}
              {_detail_row("Цель / назначение", safe_bp)}
              {_detail_row("Комментарий", safe_comment)}
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:4px 14px 10px 14px;font-family:Segoe UI,Arial,sans-serif;">
            <p style="margin:0 0 8px 0;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.06em;">Документы</p>
            {attachments_block}
          </td>
        </tr>
        <tr>
          <td style="padding:0 14px 14px 14px;">
            {actions_block}
          </td>
        </tr>
        <tr>
          <td style="padding:10px 14px;background:#f1f5f9;border-top:1px solid #e2e8f0;font-family:Segoe UI,Arial,sans-serif;font-size:11px;color:#64748b;">
            {footer_link_html}
            <p style="margin:0;">Письмо отправлено автоматически сервисом расходов Kosta Legal.</p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""


def _action_urls(
    settings: Settings,
    expense_id: str,
) -> tuple[str | None, str | None]:
    sec = (settings.expense_email_action_secret or "").strip()
    base_api = (settings.public_api_base_url or "").strip().rstrip("/")
    if not sec or not base_api:
        return None, None
    from infrastructure.email_action_token import sign_email_action_token

    try:
        ttl = int(settings.expense_email_action_ttl_seconds)
        t_ap = sign_email_action_token(sec, expense_id=expense_id, action="approve", ttl_seconds=ttl)
        t_rj = sign_email_action_token(sec, expense_id=expense_id, action="reject", ttl_seconds=ttl)
    except ValueError as e:
        _log.warning("email action links skipped: %s", e)
        return None, None

    want_confirm = bool(settings.expense_email_action_confirm_step)
    q_ap = f"token={quote(t_ap, safe='')}" + ("&confirm=1" if want_confirm else "")
    q_rj = f"token={quote(t_rj, safe='')}" + ("&confirm=1" if want_confirm else "")
    return (
        f"{base_api}/api/v1/expenses/{expense_id}/email-action?{q_ap}",
        f"{base_api}/api/v1/expenses/{expense_id}/email-action?{q_rj}",
    )


def _file_view_url(settings: Settings, expense_id: str, attachment_id: str) -> str | None:
    sec = (settings.expense_email_action_secret or "").strip()
    base_api = (settings.public_api_base_url or "").strip().rstrip("/")
    if not sec or not base_api:
        return None
    from infrastructure.email_action_token import sign_attachment_view_token

    try:
        ttl = int(settings.expense_email_action_ttl_seconds)
        tok = sign_attachment_view_token(
            sec,
            expense_id=expense_id,
            attachment_id=attachment_id,
            ttl_seconds=ttl,
        )
    except ValueError:
        return None
    return (
        f"{base_api}/api/v1/expenses/{expense_id}/attachments/{attachment_id}/email-file"
        f"?token={quote(tok, safe='')}"
    )


async def _send_moderation_message(settings: Settings, ctx: ExpenseModerationEmailContext) -> None:
    if not settings.expense_notify_on_submit:
        _log.debug("expense notify: EXPENSE_NOTIFY_ON_SUBMIT=false, skip")
        return

    if not _smtp_ready(settings):
        missing = _smtp_missing_env_names(settings)
        _log.warning(
            "expense notify: в контейнере/процессе expenses не заданы переменные %s — письмо не отправлено. "
            "Текущий EXPENSE_SMTP_HOST=%r (пустой=%s)",
            ", ".join(missing) if missing else "(неизвестно)",
            (settings.smtp_host or "")[:80],
            not bool((settings.smtp_host or "").strip()),
        )
        return
    recipients = resolve_expense_notify_recipients(
        settings,
        department_id=ctx.department_id,
        expense_type=ctx.expense_type,
        project_id=ctx.project_id,
        is_reimbursable=ctx.is_reimbursable,
    )
    if not recipients:
        _log.warning("expense notify: нет получателей (ROUTING / EXPENSE_NOTIFY_TO), skip")
        return

    expense_id = ctx.expense_id
    author_line = (ctx.author_name or "").strip() or "—"
    if ctx.author_email:
        author_line = f"{author_line} ({ctx.author_email})" if author_line != "—" else ctx.author_email

    reimb = "да" if ctx.is_reimbursable else "нет"
    desc = (ctx.description or "").strip() or "—"
    et = (ctx.expense_type or "").strip() or "—"
    sub = (ctx.expense_subtype or "").strip() or "—"
    vendor = (ctx.vendor or "").strip() or "—"
    bp = (ctx.business_purpose or "").strip() or "—"
    comment = (ctx.comment or "").strip() or "—"
    pm = (ctx.payment_method or "").strip() or "—"
    dept = (ctx.department_id or "").strip() or "—"
    proj = (ctx.project_id or "").strip() or "—"

    safe_desc = html.escape(desc).replace("\n", "<br/>")
    safe_et = html.escape(et)
    safe_sub = html.escape(sub)
    safe_vendor = html.escape(vendor)
    safe_bp = html.escape(bp).replace("\n", "<br/>")
    safe_comment = html.escape(comment).replace("\n", "<br/>")
    safe_author = html.escape(author_line)
    expense_date_fmt = _format_date(ctx.expense_date)
    pd_fmt = _format_date(ctx.payment_deadline)
    money_fmt = _format_money(ctx.amount_uzs)
    rate_fmt = _format_rate(ctx.exchange_rate)
    eq_fmt = _format_money(ctx.equivalent_amount)

    open_link = _build_open_link(settings, expense_id)
    link_plain = open_link or "(задайте FRONTEND_URL в env сервиса expenses)"

    email_ap, email_rj = _action_urls(settings, expense_id)
    if not (email_ap and email_rj):
        _log.debug(
            "email action: задайте EXPENSE_EMAIL_ACTION_SECRET и GATEWAY_BASE_URL — иначе в письме не будет кнопок согласования без входа"
        )

    media_root = Path(settings.media_path)
    inline_images: list[tuple[str, bytes, str]] = []
    file_attachments: list[tuple[str, bytes, str]] = []
    cid_counter = 0

    att_lines_plain: list[str] = []
    att_html_chunks: list[str] = []

    for att in ctx.attachments:
        view_u = _file_view_url(settings, expense_id, att.id)
        kind_ru = _kind_label(att.attachment_kind)
        label = html.escape(att.file_name or "файл")
        sz_kb = max(1, (att.size_bytes or 0) // 1024)
        att_lines_plain.append(
            f"- {att.file_name} ({kind_ru}, ~{sz_kb} KB)"
            + (f"\n  {view_u}" if view_u else "")
        )

        mt = (att.mime_type or "").strip().lower()
        p = media_root / att.storage_key
        is_any_image = mt.startswith("image/")
        is_inline_candidate = _is_inline_image_mime(mt) and p.is_file()
        if (
            is_inline_candidate
            and len(inline_images) < _INLINE_IMAGE_MAX
            and (att.size_bytes or 0) <= _INLINE_IMAGE_MAX_BYTES
        ):
            try:
                data = p.read_bytes()
            except OSError as e:
                _log.warning("expense mail: не прочитать вложение %s: %s", att.id, e)
                data = b""
            if data and len(data) <= _INLINE_IMAGE_MAX_BYTES:
                cid = f"att{cid_counter}"
                cid_counter += 1
                sub_m = mt.split("/")[-1] if "/" in mt else "jpeg"
                inline_images.append((cid, data, sub_m))
                img_tag = (
                    f'<img src="cid:{cid}" alt="{label}" '
                    f'style="max-width:100%;height:auto;border-radius:6px;display:block;border:1px solid #e2e8f0;"/>'
                )
                preview = img_tag
                if view_u:
                    su = html.escape(view_u, quote=True)
                    preview = (
                        f'<a href="{su}" target="_blank" rel="noopener noreferrer" '
                        f'style="display:block;text-decoration:none;">{img_tag}</a>'
                    )
                att_html_chunks.append(
                    _attachment_item_html(
                        kind_ru=kind_ru,
                        safe_filename=label,
                        sz_kb=sz_kb,
                        href=view_u,
                        preview_html=preview,
                    )
                )
                continue
        att_html_chunks.append(
            _attachment_item_html(
                kind_ru=kind_ru,
                safe_filename=label,
                sz_kb=sz_kb,
                href=view_u,
            )
        )
        if (
            not is_any_image
            and p.is_file()
            and (att.size_bytes or 0) <= 10 * 1024 * 1024
        ):
            try:
                raw_other = p.read_bytes()
            except OSError:
                raw_other = b""
            if raw_other:
                file_attachments.append(
                    (
                        att.file_name or "file.bin",
                        raw_other,
                        att.mime_type or "application/octet-stream",
                    )
                )
        if is_any_image and p.is_file() and (att.size_bytes or 0) > _INLINE_IMAGE_MAX_BYTES:
            try:
                data = p.read_bytes()
            except OSError:
                data = b""
            if data:
                fn = att.file_name or "attachment.bin"
                file_attachments.append((fn, data, att.mime_type or "application/octet-stream"))

    if att_html_chunks:
        attachments_block = (
            '<div style="padding:12px 14px;border:1px solid #c7d2fe;border-radius:10px;background:#fafaff;">'
            + "".join(att_html_chunks)
            + "</div>"
        )
    else:
        attachments_block = (
            '<p style="margin:0;padding:10px 12px;background:#f8fafc;border-radius:8px;color:#64748b;font-size:13px;">'
            "Файлы не прикреплены.</p>"
        )

    if email_ap and email_rj:
        actions_block = f"""
<div style="padding:12px 14px;background:linear-gradient(180deg,#eef2ff 0%,#e0e7ff 100%);border-radius:10px;border:1px solid #c7d2fe;">
  <p style="margin:0 0 4px 0;font-family:Segoe UI,Arial,sans-serif;font-size:15px;font-weight:700;color:#1e1b4b;">Решение</p>
  <p style="margin:0 0 10px 0;font-size:13px;color:#475569;line-height:1.45;">
    Нажмите кнопку: откроется короткая страница подтверждения на сервере, затем действие выполнится. Вход в приложение не нужен.
  </p>
  {_button_row_html(email_ap, "✓ Утвердить", "#16a34a")}
  {_button_row_html(email_rj, "✕ Отклонить", "#dc2626")}
  <p style="margin:10px 0 0 0;font-size:11px;color:#64748b;line-height:1.4;">
    Если кнопки не активны, скопируйте ссылку в браузер:<br/>
    <span style="word-break:break-all;color:#334155;">{html.escape(email_ap, quote=False)}</span>
  </p>
</div>"""
    else:
        actions_block = f"""
<div style="padding:12px 14px;background:#fff7ed;border-radius:10px;border:1px solid #fdba74;">
  <p style="margin:0 0 6px 0;font-size:14px;font-weight:700;color:#9a3412;">Согласование по ссылке недоступно</p>
  <p style="margin:0;font-size:13px;color:#7c2d12;line-height:1.45;">
    Задайте в env сервиса <strong>EXPENSE_EMAIL_ACTION_SECRET</strong> и <strong>GATEWAY_BASE_URL</strong>.
    Тогда появятся кнопки «Утвердить» / «Отклонить» без входа в SPA.
  </p>
  {"<p style=\"margin:8px 0 0 0;font-size:13px;\"><a href=\"" + html.escape(open_link, quote=True) + "\" style=\"color:#2563eb;font-weight:600;\">Открыть заявку в приложении</a></p>" if open_link else ""}
</div>"""

    html_body = _build_moderation_html(
        ctx=ctx,
        safe_author=safe_author,
        expense_date_fmt=expense_date_fmt,
        money_fmt=money_fmt,
        reimb=reimb,
        safe_desc=safe_desc,
        safe_et=safe_et,
        safe_sub=safe_sub,
        safe_vendor=safe_vendor,
        safe_bp=safe_bp,
        safe_comment=safe_comment,
        dept=dept,
        proj=proj,
        pm=pm,
        pd_fmt=pd_fmt,
        rate_fmt=rate_fmt,
        eq_fmt=eq_fmt,
        open_link=open_link,
        actions_block=actions_block,
        attachments_block=attachments_block,
    )

    subject = f"Заявка на расход {expense_id} — на согласование"
    plain_lines = [
        f"Заявка на согласование: {expense_id}",
        f"Автор: {author_line}",
        f"Дата расхода: {expense_date_fmt}",
        f"Срок оплаты: {pd_fmt}",
        f"Тип: {et} / {sub}",
        f"Сумма (UZS): {money_fmt}",
        f"Курс: {rate_fmt}",
        f"Эквивалент: {eq_fmt}",
        f"Возмещаемый: {reimb}",
        f"Контрагент: {vendor}",
        f"Описание: {desc}",
        "",
        "Вложения:",
        *(att_lines_plain if att_lines_plain else ["(нет)"]),
        "",
    ]
    if email_ap and email_rj:
        plain_lines.extend(
            [
                "Утвердить (сервер, без SPA):",
                email_ap,
                "",
                "Отклонить (сервер, без SPA):",
                email_rj,
                "",
            ]
        )
    if open_link:
        plain_lines.extend(["Открыть в приложении (опционально):", link_plain, ""])
    plain_lines.append("— Kosta Legal / расходы")

    text_body = "\n".join(plain_lines)

    msg_root = MIMEMultipart("mixed")
    msg_root["Subject"] = subject
    from_addr = (settings.expense_mail_from or settings.smtp_user or "").strip()
    if not from_addr:
        _log.warning(
            "expense notify: пустой отправитель — задайте EXPENSE_MAIL_FROM или EXPENSE_SMTP_USER, expense_id=%s",
            expense_id,
        )
        return
    msg_root["From"] = from_addr
    msg_root["To"] = ", ".join(recipients)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(text_body, "plain", "utf-8"))

    if inline_images:
        related = MIMEMultipart("related")
        related.attach(MIMEText(html_body, "html", "utf-8"))
        for cid, raw, sub in inline_images:
            img = MIMEImage(raw, _subtype=sub)
            img.add_header("Content-ID", f"<{cid}>")
            img.add_header("Content-Disposition", "inline", filename=f"{cid}.{sub}")
            related.attach(img)
        alt.attach(related)
    else:
        alt.attach(MIMEText(html_body, "html", "utf-8"))

    msg_root.attach(alt)

    for fname, raw, mtype in file_attachments:
        maintype, _, subtype = mtype.partition("/")
        if not subtype:
            maintype, subtype = "application", "octet-stream"
        part = MIMEBase(maintype, subtype)
        part.set_payload(raw)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=fname)
        msg_root.attach(part)

    _log.info(
        "expense notify: отправка SMTP expense_id=%s to=%s host=%s",
        expense_id,
        recipients,
        settings.smtp_host.strip(),
    )
    try:
        await aiosmtplib.send(
            msg_root,
            hostname=settings.smtp_host.strip(),
            port=int(settings.smtp_port),
            username=settings.smtp_user.strip(),
            password=settings.smtp_password,
            start_tls=bool(settings.smtp_use_tls),
        )
    except Exception as e:
        _log.error(
            "expense notify: ошибка SMTP expense_id=%s: %s: %s",
            expense_id,
            type(e).__name__,
            e,
        )
        raise
    _log.info("expense moderation mail sent to %s expense_id=%s", recipients, expense_id)


async def notify_partner_expense_recorded(settings: Settings, ctx: ExpenseModerationEmailContext) -> None:

    if not _smtp_ready(settings):
        _log.warning(
            "expense partner notify: SMTP не настроен (%s), expense_id=%s",
            ", ".join(_smtp_missing_env_names(settings)),
            ctx.expense_id,
        )
        return
    recipients = resolve_expense_notify_recipients(
        settings,
        department_id=ctx.department_id,
        expense_type=ctx.expense_type,
        project_id=ctx.project_id,
        is_reimbursable=ctx.is_reimbursable,
    )
    if not recipients:
        _log.warning("expense partner notify: нет получателей (ROUTING / EXPENSE_NOTIFY_TO), skip")
        return

    expense_id = ctx.expense_id
    author_line = (ctx.author_name or "").strip() or "—"
    if ctx.author_email:
        author_line = f"{author_line} ({ctx.author_email})" if author_line != "—" else ctx.author_email

    desc = (ctx.description or "").strip() or "—"
    et = (ctx.expense_type or "").strip() or "—"
    sub = (ctx.expense_subtype or "").strip() or "—"
    vendor = (ctx.vendor or "").strip() or "—"
    expense_date_fmt = _format_date(ctx.expense_date)
    pd_fmt = _format_date(ctx.payment_deadline)
    money_fmt = _format_money(ctx.amount_uzs)
    rate_fmt = _format_rate(ctx.exchange_rate)
    eq_fmt = _format_money(ctx.equivalent_amount)
    reimb = "да" if ctx.is_reimbursable else "нет"
    dept = (ctx.department_id or "").strip() or "—"
    proj = (ctx.project_id or "").strip() or "—"

    open_link = _build_open_link(settings, expense_id)
    link_plain = open_link or ""
    link_html = ""
    if open_link:
        su = html.escape(open_link, quote=True)
        link_html = f'<p style="margin:16px 0 0 0;"><a href="{su}" style="color:#2563eb;font-weight:600;">Открыть в системе</a></p>'

    safe_id = html.escape(expense_id)
    subject = f"Расход партнёра {expense_id} — учтён в системе"

    plain_lines = [
        f"В систему добавлен расход партнёра {expense_id} (без согласования).",
        f"Автор: {author_line}",
        f"Дата расхода: {expense_date_fmt}",
        f"Срок оплаты: {pd_fmt}",
        f"Подтип: {sub}",
        f"Сумма (UZS): {money_fmt}",
        f"Курс: {rate_fmt}",
        f"Эквивалент (USD): {eq_fmt}",
        f"Возмещаемый: {reimb}",
        f"Подразделение: {dept}",
        f"Проект: {proj}",
        f"Контрагент: {vendor}",
        f"Описание: {desc}",
    ]
    if link_plain:
        plain_lines.extend(["", f"Ссылка: {link_plain}"])
    plain_lines.extend(["", "— Kosta Legal · расходы"])
    text_body = "\n".join(plain_lines)

    html_body = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"/></head>
<body style="margin:0;font-family:Segoe UI,Arial,sans-serif;background:#f8fafc;color:#0f172a;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f8fafc;padding:24px 12px;">
  <tr><td align="center">
    <table role="presentation" width="560" cellspacing="0" cellpadding="0" border="0" style="max-width:560px;background:#ffffff;border-radius:12px;border:1px solid #e2e8f0;padding:28px 24px;">
      <tr><td>
        <p style="margin:0 0 8px 0;font-size:16px;font-weight:700;color:#0f172a;">Расход партнёра учтён</p>
        <p style="margin:0 0 16px 0;font-size:14px;line-height:1.55;color:#334155;">
          Запись <strong>{safe_id}</strong> добавлена в систему. Согласование не требуется.
        </p>
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">
          {_detail_row("Автор", html.escape(author_line))}
          {_detail_row("Дата расхода", html.escape(expense_date_fmt))}
          {_detail_row("Срок оплаты", html.escape(pd_fmt))}
          {_detail_row("Тип / подтип", html.escape(f"{et} / {sub}"))}
          {_detail_row("Сумма (UZS)", html.escape(money_fmt))}
          {_detail_row("Курс", html.escape(rate_fmt))}
          {_detail_row("Эквивалент", html.escape(eq_fmt))}
          {_detail_row("Возмещаемый", html.escape(reimb))}
          {_detail_row("Подразделение", html.escape(dept))}
          {_detail_row("Проект", html.escape(proj))}
          {_detail_row("Контрагент", html.escape(vendor))}
          {_detail_row("Описание", html.escape(desc).replace("\n", "<br/>"))}
        </table>
        {link_html}
        <p style="margin:24px 0 0 0;font-size:13px;color:#64748b;">Kosta Legal · расходы</p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""

    from_addr = (settings.expense_mail_from or settings.smtp_user or "").strip()
    if not from_addr:
        _log.warning(
            "expense partner notify: пустой отправитель — задайте EXPENSE_MAIL_FROM или EXPENSE_SMTP_USER, expense_id=%s",
            expense_id,
        )
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    _log.info(
        "expense partner notify: отправка SMTP expense_id=%s to=%s host=%s",
        expense_id,
        recipients,
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
            "expense partner notify: ошибка SMTP expense_id=%s: %s: %s",
            expense_id,
            type(e).__name__,
            e,
        )
        raise
    _log.info("expense partner notify: отправлено expense_id=%s", expense_id)


async def notify_expense_submitted(settings: Settings, ctx: ExpenseModerationEmailContext) -> None:

    await _send_moderation_message(settings, ctx)


async def notify_expense_author_decision(
    settings: Settings,
    *,
    to_email: str,
    display_name: str | None,
    expense_id: str,
    decision: Literal["approved", "rejected"],
    reject_reason: str | None,
) -> None:

    if not _smtp_ready(settings):
        _log.warning(
            "expense author notify: SMTP не настроен (%s), expense_id=%s",
            ", ".join(_smtp_missing_env_names(settings)),
            expense_id,
        )
        return
    to = (to_email or "").strip()
    if not to:
        _log.warning("expense author notify: пустой email, expense_id=%s", expense_id)
        return

    safe_id = html.escape(expense_id)
    greeting = (display_name or "").strip() or "Здравствуйте"
    safe_greeting = html.escape(greeting)

    comment_html = ""
    if decision == "approved":
        subject = f"Заявка {expense_id} утверждена"
        lead = f"Ваша заявка на расход <strong>{safe_id}</strong> <strong>утверждена</strong>."
        plain_lead = f"Ваша заявка на расход {expense_id} утверждена."
    else:
        subject = f"Заявка {expense_id} отклонена"
        lead = f"Ваша заявка на расход <strong>{safe_id}</strong> <strong>отклонена</strong>."
        plain_lead = f"Ваша заявка на расход {expense_id} отклонена."
        if reject_reason and str(reject_reason).strip():
            r = html.escape(str(reject_reason).strip())
            comment_html = (
                f'<p style="margin:16px 0 0 0;color:#0f172a;font-size:14px;">'
                f"<strong>Комментарий:</strong> {r}</p>"
            )
            plain_lead += f"\n\nКомментарий: {str(reject_reason).strip()}"

    open_link = _build_open_link(settings, expense_id)
    link_block_html = ""
    link_block_plain = ""
    if open_link:
        safe_link = html.escape(open_link, quote=True)
        link_block_html = f"""
<p style="margin:20px 0 0 0;">
  <a href="{safe_link}" style="color:#2563eb;font-weight:600;">Открыть заявку в системе</a>
</p>"""
        link_block_plain = f"\n\nСсылка: {open_link}"

    html_body = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"/></head>
<body style="margin:0;font-family:Segoe UI,Arial,sans-serif;background:#f8fafc;color:#0f172a;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f8fafc;padding:24px 12px;">
  <tr><td align="center">
    <table role="presentation" width="560" cellspacing="0" cellpadding="0" border="0" style="max-width:560px;background:#ffffff;border-radius:12px;border:1px solid #e2e8f0;padding:28px 24px;">
      <tr><td>
        <p style="margin:0 0 12px 0;font-size:15px;color:#0f172a;">{safe_greeting},</p>
        <p style="margin:0 0 8px 0;font-size:15px;line-height:1.55;color:#334155;">{lead}</p>
        {comment_html}
        {link_block_html}
        <p style="margin:24px 0 0 0;font-size:13px;color:#64748b;">Kosta Legal · расходы</p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""

    plain_body = f"""{greeting},

{plain_lead}{link_block_plain}
"""
    from_addr = (settings.expense_mail_from or settings.smtp_user or "").strip()
    if not from_addr:
        _log.warning(
            "expense author notify: пустой отправитель — задайте EXPENSE_MAIL_FROM или EXPENSE_SMTP_USER, expense_id=%s",
            expense_id,
        )
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    _log.info(
        "expense author notify: отправка SMTP expense_id=%s decision=%s to=%s host=%s",
        expense_id,
        decision,
        to,
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
            "expense author notify: ошибка SMTP expense_id=%s: %s: %s",
            expense_id,
            type(e).__name__,
            e,
        )
        raise
    _log.info("expense author notify: отправлено expense_id=%s to=%s", expense_id, to)


async def notify_expense_author_paid(
    settings: Settings,
    *,
    to_email: str,
    display_name: str | None,
    expense_id: str,
    paid_by_line: str,
) -> None:

    if not _smtp_ready(settings):
        _log.warning(
            "expense author paid notify: SMTP не настроен (%s), expense_id=%s",
            ", ".join(_smtp_missing_env_names(settings)),
            expense_id,
        )
        return
    to = (to_email or "").strip()
    if not to:
        _log.warning("expense author paid notify: пустой email, expense_id=%s", expense_id)
        return

    safe_id = html.escape(expense_id)
    greeting = (display_name or "").strip() or "Здравствуйте"
    safe_greeting = html.escape(greeting)
    safe_paid_by = html.escape((paid_by_line or "").strip() or "—")

    subject = f"Заявка {expense_id} оплачена"
    lead = (
        f"Ваша заявка на расход <strong>{safe_id}</strong> отмечена как <strong>оплаченная</strong> "
        f"со стороны компании."
    )
    who = (
        f'<p style="margin:16px 0 0 0;color:#0f172a;font-size:14px;">'
        f"<strong>Оплату отметил(а):</strong> {safe_paid_by}</p>"
    )
    plain_lead = (
        f"Ваша заявка на расход {expense_id} отмечена как оплаченная.\n\n"
        f"Оплату отметил(а): {(paid_by_line or '').strip() or '—'}"
    )

    open_link = _build_open_link(settings, expense_id)
    link_block_html = ""
    link_block_plain = ""
    if open_link:
        safe_link = html.escape(open_link, quote=True)
        link_block_html = f"""
<p style="margin:20px 0 0 0;">
  <a href="{safe_link}" style="color:#2563eb;font-weight:600;">Открыть заявку в системе</a>
</p>"""
        link_block_plain = f"\n\nСсылка: {open_link}"

    html_body = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"/></head>
<body style="margin:0;font-family:Segoe UI,Arial,sans-serif;background:#f8fafc;color:#0f172a;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f8fafc;padding:24px 12px;">
  <tr><td align="center">
    <table role="presentation" width="560" cellspacing="0" cellpadding="0" border="0" style="max-width:560px;background:#ffffff;border-radius:12px;border:1px solid #e2e8f0;padding:28px 24px;">
      <tr><td>
        <p style="margin:0 0 12px 0;font-size:15px;color:#0f172a;">{safe_greeting},</p>
        <p style="margin:0 0 8px 0;font-size:15px;line-height:1.55;color:#334155;">{lead}</p>
        {who}
        {link_block_html}
        <p style="margin:24px 0 0 0;font-size:13px;color:#64748b;">Kosta Legal · расходы</p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""

    plain_body = f"""{greeting},

{plain_lead}{link_block_plain}
"""
    from_addr = (settings.expense_mail_from or settings.smtp_user or "").strip()
    if not from_addr:
        _log.warning(
            "expense author paid notify: пустой отправитель — задайте EXPENSE_MAIL_FROM или EXPENSE_SMTP_USER, expense_id=%s",
            expense_id,
        )
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    _log.info(
        "expense author paid notify: отправка SMTP expense_id=%s to=%s host=%s",
        expense_id,
        to,
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
            "expense author paid notify: ошибка SMTP expense_id=%s: %s: %s",
            expense_id,
            type(e).__name__,
            e,
        )
        raise
    _log.info("expense author paid notify: отправлено expense_id=%s to=%s", expense_id, to)


async def send_expense_smtp_test(settings: Settings) -> list[str]:

    if not _smtp_ready(settings):
        raise ValueError("Задайте EXPENSE_SMTP_HOST, EXPENSE_SMTP_USER, EXPENSE_SMTP_PASSWORD")

    ctx = ExpenseModerationEmailContext(
        expense_id="TEST-EXPENSE",
        description="Тестовое описание для проверки SMTP.",
        expense_date=date(2099, 1, 1),
        payment_deadline=None,
        amount_uzs=Decimal("1234567.89"),
        exchange_rate=Decimal("12500"),
        equivalent_amount=Decimal("98.76"),
        expense_type="office",
        expense_subtype=None,
        is_reimbursable=True,
        payment_method="card",
        department_id="dept-1",
        project_id="proj-1",
        vendor="ООО Тест",
        business_purpose="Проверка вёрстки письма",
        comment="—",
        author_email="author@example.com",
        author_name="Тестовый автор",
        attachments=[],
    )
    recipients = resolve_expense_notify_recipients(
        settings,
        department_id=ctx.department_id,
        expense_type=ctx.expense_type,
        project_id=ctx.project_id,
        is_reimbursable=ctx.is_reimbursable,
    )
    if not recipients:
        raise ValueError("Нет получателей: задайте EXPENSE_NOTIFY_ROUTING_JSON или EXPENSE_NOTIFY_TO")

    await _send_moderation_message(settings, ctx)
    _log.info("expense SMTP test sent to %s", recipients)
    return recipients
