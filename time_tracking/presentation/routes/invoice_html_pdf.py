"""Server-side printing of the invoice preview so the PDF matches the screen 1:1."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import Response

from application.html_pdf import MAX_HTML_BYTES, HtmlPdfUnavailable, render_html_to_pdf

router = APIRouter(prefix="/invoice-pdf", tags=["invoice_html_pdf"])


@router.post("/render")
async def render_invoice_pdf(request: Request) -> Response:
    body = await request.body()
    if not body.strip():
        raise HTTPException(status_code=400, detail="Пустая страница")
    if len(body) > MAX_HTML_BYTES:
        raise HTTPException(status_code=413, detail="Страница слишком большая для PDF")
    try:
        pdf = await render_html_to_pdf(body.decode("utf-8", errors="replace"))
    except HtmlPdfUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
