from __future__ import annotations

import logging
from typing import Annotated
from urllib.parse import quote, urlparse, parse_qs

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import get_session
from infrastructure.microsoft_graph import create_mail_draft
from infrastructure.repositories import OutlookCalendarTokenRepository
from presentation.dependencies import get_current_user_id
from presentation.routes.calendar_routes import _get_valid_token

router = APIRouter(prefix="/outlook", tags=["outlook"])
_log = logging.getLogger(__name__)


class OutlookMailDraftBody(BaseModel):
    toEmail: str = Field(..., min_length=3)
    toName: str | None = None
    subject: str = Field(default="", max_length=998)
    bodyHtml: str | None = None
    bodyText: str | None = None
    pdfBase64: str | None = None
    pdfFileName: str | None = None


def _outlook_compose_web_link(message_id: str | None, graph_web_link: str | None) -> str | None:
    """
    Graph webLink opens drafts in read mode (Reply/Forward, no Send).
    Prefer the compose deeplink so the user can edit and send.
    """
    mid = (message_id or "").strip()
    if mid:
        q = quote(mid, safe="")
        return f"https://outlook.office.com/mail/deeplink/compose/{q}?ItemID={q}&exvsurl=1"

    link = (graph_web_link or "").strip()
    if not link:
        return None
    # Convert read deeplink → compose when Graph only gave webLink.
    if "/deeplink/read/" in link:
        return link.replace("/deeplink/read/", "/deeplink/compose/", 1)
    if "/deeplink/compose/" in link:
        return link
    # Try to extract ItemID from query and rebuild.
    try:
        parsed = urlparse(link)
        qs = parse_qs(parsed.query)
        item = (qs.get("ItemID") or qs.get("itemid") or [None])[0]
        if item:
            q = quote(str(item), safe="")
            return f"https://outlook.office.com/mail/deeplink/compose/{q}?ItemID={q}&exvsurl=1"
    except Exception:
        pass
    return link


@router.post("/mail-draft", summary="Создать черновик письма в Outlook с опциональным PDF")
async def create_outlook_mail_draft(
    body: OutlookMailDraftBody,
    user_id: Annotated[int, Depends(get_current_user_id)],
    session: AsyncSession = Depends(get_session),
):
    repo = OutlookCalendarTokenRepository(session)
    row = await _get_valid_token(repo, user_id, session)
    if not row:
        raise HTTPException(
            status_code=409,
            detail=(
                "Outlook не подключён. Подключите календарь Outlook "
                "(раздел To-Do / расписание), затем повторите отправку счёта."
            ),
        )
    try:
        msg = await create_mail_draft(
            row.access_token,
            to_email=body.toEmail,
            to_name=body.toName,
            subject=body.subject,
            body_html=body.bodyHtml,
            body_text=body.bodyText,
            pdf_base64=body.pdfBase64,
            pdf_file_name=body.pdfFileName,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(
            status_code=403,
            detail=(
                f"{e} "
                "Отключите и снова подключите Outlook, чтобы выдать право Mail.ReadWrite."
            ),
        ) from e
    except Exception as e:
        _log.exception("create_outlook_mail_draft failed for user_id=%s", user_id)
        raise HTTPException(status_code=502, detail=f"Outlook mail API error: {e!s}") from e

    graph_web_link = msg.get("webLink") if isinstance(msg, dict) else None
    message_id = msg.get("id") if isinstance(msg, dict) else None
    web_link = _outlook_compose_web_link(
        str(message_id) if message_id is not None else None,
        str(graph_web_link) if graph_web_link is not None else None,
    )
    if not web_link:
        raise HTTPException(
            status_code=502,
            detail="Outlook создал черновик, но не вернул ссылку для открытия.",
        )
    return {
        "webLink": web_link,
        "messageId": message_id,
    }
