from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from infrastructure.models_invoices import InvoicePdfFileModel


class InvoicePdfFileRepository:
    """Uploaded invoice PDFs: one current file per invoice, older versions kept but hidden."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_current(self, invoice_id: str, *, with_content: bool = False) -> InvoicePdfFileModel | None:
        stmt = select(InvoicePdfFileModel).where(
            InvoicePdfFileModel.invoice_id == invoice_id,
            InvoicePdfFileModel.is_current.is_(True),
        )
        if with_content:
            stmt = stmt.options(undefer(InvoicePdfFileModel.content))
        return (await self._s.execute(stmt)).scalars().first()

    async def current_meta_by_invoice(self, invoice_ids: list[str]) -> dict[str, InvoicePdfFileModel]:
        """Current file metadata (no bytes) for many invoices at once — for registry sheets."""
        ids = [i for i in invoice_ids if i]
        if not ids:
            return {}
        stmt = select(InvoicePdfFileModel).where(
            InvoicePdfFileModel.invoice_id.in_(ids),
            InvoicePdfFileModel.is_current.is_(True),
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return {row.invoice_id: row for row in rows}

    async def replace(
        self,
        *,
        invoice_id: str,
        file_name: str,
        content: bytes,
        uploaded_by_auth_user_id: int,
        content_type: str = "application/pdf",
    ) -> tuple[InvoicePdfFileModel, InvoicePdfFileModel | None]:
        """Make ``content`` the current PDF. The previous current row is hidden, never deleted.

        Returns ``(new_row, previous_row_or_None)``. The caller commits.
        """
        now = datetime.now(timezone.utc)
        previous = await self.get_current(invoice_id)
        new_id = str(uuid.uuid4())
        if previous is not None:
            await self._s.execute(
                update(InvoicePdfFileModel)
                .where(InvoicePdfFileModel.id == previous.id)
                .values(is_current=False, replaced_at=now, replaced_by_file_id=new_id)
            )
            # The partial unique index allows only one current row: flush the demotion first.
            await self._s.flush()
        row = InvoicePdfFileModel(
            id=new_id,
            invoice_id=invoice_id,
            file_name=file_name,
            content_type=content_type,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            content=content,
            is_current=True,
            uploaded_by_auth_user_id=uploaded_by_auth_user_id,
            uploaded_at=now,
        )
        self._s.add(row)
        await self._s.flush()
        return row, previous
