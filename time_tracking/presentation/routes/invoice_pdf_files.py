"""Manually uploaded invoice PDFs (registry «2026 (система)»).

The current file replaces the generated PDF in the UI. Replacing keeps the previous file in
``time_tracking_invoice_pdf_files`` (``is_current = false``) — it is never listed or served
by the API and can be recovered only via SQL.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from application.invoice_pdf_files import (
    InvoicePdfValidationError,
    MAX_INVOICE_PDF_BYTES,
    content_disposition,
    safe_pdf_file_name,
    validate_invoice_pdf,
)
from infrastructure.database import get_session
from infrastructure.models_invoices import InvoiceAuditLogModel, InvoicePdfFileModel
from infrastructure.repository_invoice_pdf_files import InvoicePdfFileRepository
from infrastructure.repository_invoices import InvoiceRepository
from presentation.deps import invoice_actor_auth_user_id

router = APIRouter(prefix="/invoices", tags=["invoice_pdf_files"])


def pdf_file_meta(row: InvoicePdfFileModel | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "fileName": row.file_name,
        "sizeBytes": row.size_bytes,
        "uploadedAt": row.uploaded_at.isoformat() if row.uploaded_at else None,
        "uploadedByAuthUserId": row.uploaded_by_auth_user_id,
    }


async def _require_invoice(session: AsyncSession, invoice_id: str):
    inv = await InvoiceRepository(session).get(invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    return inv


@router.get("/{invoice_id}/pdf-file")
async def get_invoice_pdf_file_meta(
    invoice_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Metadata of the current uploaded PDF, or ``{"current": null}`` — then the UI generates it."""
    row = await InvoicePdfFileRepository(session).get_current(invoice_id)
    return {"current": pdf_file_meta(row)}


@router.get("/{invoice_id}/pdf-file/content")
async def get_invoice_pdf_file_content(
    invoice_id: str,
    download: bool = Query(False),
    session: AsyncSession = Depends(get_session),
):
    row = await InvoicePdfFileRepository(session).get_current(invoice_id, with_content=True)
    if row is None:
        raise HTTPException(status_code=404, detail="Загруженного PDF нет")
    return Response(
        content=row.content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(row.file_name, "attachment" if download else "inline"),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.put("/{invoice_id}/pdf-file")
async def replace_invoice_pdf_file(
    invoice_id: str,
    request: Request,
    file_name: str | None = Query(None, alias="fileName"),
    actor_id: int = Depends(invoice_actor_auth_user_id),
    session: AsyncSession = Depends(get_session),
):
    """Upload a new PDF (raw ``application/pdf`` body). The previous one is kept but hidden."""
    inv = await _require_invoice(session, invoice_id)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_INVOICE_PDF_BYTES:
        raise HTTPException(status_code=413, detail="Файл слишком большой")
    content = await request.body()
    try:
        validate_invoice_pdf(content)
    except InvoicePdfValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    fallback = f"{(inv.invoice_number or invoice_id).strip()}.pdf"
    name = safe_pdf_file_name(file_name, fallback)
    repo = InvoicePdfFileRepository(session)
    row, previous = await repo.replace(
        invoice_id=invoice_id,
        file_name=name,
        content=content,
        uploaded_by_auth_user_id=actor_id,
    )
    InvoiceRepository(session).add_audit(
        InvoiceAuditLogModel(
            invoice_id=invoice_id,
            action="pdf_replaced",
            detail=json.dumps(
                {
                    "fileId": row.id,
                    "fileName": row.file_name,
                    "sizeBytes": row.size_bytes,
                    "sha256": row.sha256,
                    "previousFileId": previous.id if previous else None,
                },
                ensure_ascii=False,
            ),
            actor_auth_user_id=actor_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    return {"current": pdf_file_meta(row)}
