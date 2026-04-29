

from __future__ import annotations

import html as html_mod
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import FileResponse

from infrastructure.config import get_settings
from infrastructure.expense_author_decision_notify import run_author_decision_notification_safe
from infrastructure.database import get_session
from infrastructure.email_action_token import verify_attachment_view_token, verify_email_action_token
from infrastructure.repositories import ExpenseRepository

router = APIRouter(prefix="/expenses", tags=["expenses"])


_EMAIL_ACTION_USER_ID = 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _confirm_flag(raw: str | None) -> bool:
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _final_action_url_public(
    settings,
    *,
    expense_id: str,
    token: str,
    request: Request,
) -> str:

    final_q = f"token={quote(token, safe='')}"
    pub = (settings.public_api_base_url or "").strip().rstrip("/")
    if pub:
        return f"{pub}/api/v1/expenses/{expense_id}/email-action?{final_q}"
    return f"{request.url.scheme}://{request.url.netloc}{request.url.path}?{final_q}"


def _page(title: str, message: str, ok: bool) -> str:
    color = "#16a34a" if ok else "#dc2626"
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{title}</title></head>
<body style="margin:0;font-family:Segoe UI,Arial,sans-serif;background:linear-gradient(160deg,#0f172a 0%,#1e293b 100%);color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;">
<div style="max-width:480px;text-align:center;background:rgba(15,23,42,.6);border-radius:16px;padding:32px 28px;border:1px solid #334155;box-shadow:0 25px 50px -12px rgba(0,0,0,.45);">
<p style="color:{color};font-size:56px;margin:0 0 12px 0;line-height:1;">{'✓' if ok else '!'}</p>
<h1 style="font-size:22px;margin:0 0 16px 0;font-weight:600;letter-spacing:-.02em;">{title}</h1>
<p style="color:#cbd5e1;font-size:16px;line-height:1.55;margin:0;">{message}</p>
<p style="color:#64748b;font-size:13px;margin:28px 0 0 0;">Kosta Legal · расходы</p>
<p style="color:#475569;font-size:12px;margin:12px 0 0 0;">Можно закрыть эту вкладку — переход в приложение не требуется.</p>
</div></body></html>"""


def _confirm_html(
    *,
    expense_id: str,
    action: str,
    final_url: str,
    cancel_hint: str,
) -> str:
    verb = "утвердить" if action == "approve" else "отклонить"
    title_verb = "Утвердить" if action == "approve" else "Отклонить"
    accent = "#16a34a" if action == "approve" else "#dc2626"
    safe_eid = html_mod.escape(expense_id)
    safe_final = html_mod.escape(final_url, quote=True)
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Подтверждение · {safe_eid}</title></head>
<body style="margin:0;font-family:Segoe UI,Arial,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;">
<div style="max-width:440px;width:100%;background:#1e293b;border-radius:16px;padding:28px;border:1px solid #334155;">
<p style="margin:0 0 8px 0;font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:.06em;">Подтверждение</p>
<h1 style="margin:0 0 12px 0;font-size:22px;font-weight:600;">{title_verb} заявку {safe_eid}?</h1>
<p style="margin:0 0 24px 0;font-size:15px;color:#cbd5e1;line-height:1.5;">После нажатия кнопки заявка будет <strong>{verb}</strong> через сервер. Это не страница приложения — отдельного входа не требуется.</p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
<tr><td style="padding:0 0 12px 0;">
<a href="{safe_final}" style="display:block;text-align:center;padding:16px 20px;background:{accent};color:#fff !important;text-decoration:none;border-radius:10px;font-weight:600;font-size:16px;">Да, {title_verb.lower()}</a>
</td></tr>
<tr><td style="padding:0;">
<p style="margin:0;font-size:13px;color:#64748b;text-align:center;">{html_mod.escape(cancel_hint)}</p>
</td></tr>
</table>
</div></body></html>"""


@router.get("/{expense_id}/email-action", response_class=HTMLResponse)
async def expense_email_action(
    request: Request,
    expense_id: str,
    token: str = Query(..., description="Подписанный токен из письма"),
    confirm: str | None = Query(None, description="1 — только экран подтверждения, без действия"),
    session: AsyncSession = Depends(get_session),
):
    settings = get_settings()
    secret = (settings.expense_email_action_secret or "").strip()
    if not secret:
        return HTMLResponse(
            _page("Ссылка недоступна", "На сервере не задан EXPENSE_EMAIL_ACTION_SECRET.", False),
            status_code=503,
        )
    try:
        action = verify_email_action_token(secret, token=token, expense_id=expense_id)
    except ValueError as e:
        return HTMLResponse(
            _page("Ссылка недействительна", str(e), False),
            status_code=400,
        )

    repo = ExpenseRepository(session)
    row = await repo.get_by_id(expense_id, load_children=True)
    if not row:
        return HTMLResponse(_page("Заявка не найдена", f"Нет заявки {expense_id}.", False), status_code=404)

    if _confirm_flag(confirm) and settings.expense_email_action_confirm_step:
        final_url = _final_action_url_public(settings, expense_id=expense_id, token=token, request=request)
        return HTMLResponse(
            _confirm_html(
                expense_id=expense_id,
                action=action,
                final_url=final_url,
                cancel_hint="Если передумали — просто закройте вкладку.",
            ),
            status_code=200,
        )

    if row.status == "approved" and action == "approve":
        return HTMLResponse(
            _page("Уже утверждено", f"Заявка {expense_id} уже была утверждена.", True),
            status_code=200,
        )
    if row.status == "rejected" and action == "reject":
        return HTMLResponse(
            _page("Уже отклонено", f"Заявка {expense_id} уже была отклонена.", True),
            status_code=200,
        )
    if row.status != "pending_approval":
        return HTMLResponse(
            _page(
                "Действие недоступно",
                f"Статус заявки: «{row.status}». Согласование по ссылке возможно только в статусе «на согласовании».",
                False,
            ),
            status_code=409,
        )

    uid = _EMAIL_ACTION_USER_ID
    prev = row.status

    if action == "approve":
        row.status = "approved"
        row.approved_at = _utc_now()
        row.updated_by_user_id = uid
        row.updated_at = _utc_now()
        await repo.add_status_history(
            expense_request_id=row.id,
            from_status=prev,
            to_status="approved",
            changed_by_user_id=uid,
            comment=None,
        )
        await repo.add_audit(
            expense_request_id=row.id,
            action="approved_via_email_link",
            field_name="status",
            old_value=prev,
            new_value="approved",
            performed_by_user_id=uid,
        )
        await session.commit()
        await run_author_decision_notification_safe(
            settings,
            authorization=None,
            author_user_id=row.created_by_user_id,
            expense_id=row.id,
            decision="approved",
            reject_reason=None,
        )
        return HTMLResponse(
            _page("Заявка утверждена", f"Расход {expense_id} отмечен как утверждённый.", True),
            status_code=200,
        )

    reason = "Отклонено по ссылке из письма"
    row.status = "rejected"
    row.rejected_at = _utc_now()
    row.updated_by_user_id = uid
    row.updated_at = _utc_now()
    await repo.add_status_history(
        expense_request_id=row.id,
        from_status=prev,
        to_status="rejected",
        changed_by_user_id=uid,
        comment=reason,
    )
    await repo.add_audit(
        expense_request_id=row.id,
        action="rejected_via_email_link",
        field_name="status",
        old_value=prev,
        new_value=f"rejected: {reason}",
        performed_by_user_id=uid,
    )
    await session.commit()
    await run_author_decision_notification_safe(
        settings,
        authorization=None,
        author_user_id=row.created_by_user_id,
        expense_id=row.id,
        decision="rejected",
        reject_reason=reason,
    )
    return HTMLResponse(
        _page("Заявка отклонена", f"Расход {expense_id} отклонён.", True),
        status_code=200,
    )


@router.get("/{expense_id}/attachments/{attachment_id}/email-file")
async def expense_attachment_email_file(
    expense_id: str,
    attachment_id: str,
    token: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    settings = get_settings()
    secret = (settings.expense_email_action_secret or "").strip()
    if not secret:
        return HTMLResponse(
            _page("Ссылка недоступна", "На сервере не задан EXPENSE_EMAIL_ACTION_SECRET.", False),
            status_code=503,
        )
    try:
        verify_attachment_view_token(
            secret,
            token=token,
            expense_id=expense_id,
            attachment_id=attachment_id,
        )
    except ValueError as e:
        return HTMLResponse(
            _page("Ссылка недействительна", str(e), False),
            status_code=400,
        )

    repo = ExpenseRepository(session)
    row = await repo.get_by_id(expense_id, load_children=True)
    if not row:
        return HTMLResponse(_page("Заявка не найдена", f"Нет заявки {expense_id}.", False), status_code=404)

    att_row = next((a for a in (row.attachments or []) if a.id == attachment_id), None)
    if not att_row:
        return HTMLResponse(_page("Файл не найден", "Вложение отсутствует.", False), status_code=404)

    p = Path(settings.media_path) / att_row.storage_key
    if not p.is_file():
        return HTMLResponse(_page("Файл не найден", "Файл на диске отсутствует.", False), status_code=404)

    media = (att_row.mime_type or "").strip() or "application/octet-stream"
    return FileResponse(
        path=p,
        filename=att_row.file_name or "attachment",
        media_type=media,
        content_disposition_type="inline",
    )
