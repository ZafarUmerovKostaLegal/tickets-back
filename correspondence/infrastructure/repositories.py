from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from application.correspondence_service import format_registry_number
from infrastructure.models import (
    CorrespondenceAttachmentModel,
    CorrespondenceDocumentModel,
    CorrespondenceRegistryCounterModel,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CorrespondenceRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def next_registry_number(self, direction: str, year: int) -> str:
        q = (
            select(CorrespondenceRegistryCounterModel)
            .where(
                CorrespondenceRegistryCounterModel.direction == direction,
                CorrespondenceRegistryCounterModel.year == year,
            )
            .with_for_update()
        )
        r = await self._session.execute(q)
        row = r.scalars().one_or_none()
        if row is None:
            row = CorrespondenceRegistryCounterModel(direction=direction, year=year, last_seq=0)
            self._session.add(row)
            await self._session.flush()
        row.last_seq += 1
        await self._session.flush()
        return format_registry_number(direction, year, row.last_seq)

    async def create_document(
        self,
        *,
        id_: str,
        registry_number: str,
        direction: str,
        doc_type: str,
        status: str,
        counterparty: str,
        subject: str,
        comment: str | None,
        partner_user_id: int | None,
        responsible_user_id: int,
        registered_at: datetime | None = None,
    ) -> CorrespondenceDocumentModel:
        now = registered_at or _utc_now()
        row = CorrespondenceDocumentModel(
            id=id_,
            registry_number=registry_number,
            direction=direction,
            doc_type=doc_type,
            status=status,
            counterparty=counterparty.strip(),
            subject=subject.strip(),
            comment=(comment or None),
            partner_user_id=partner_user_id,
            responsible_user_id=responsible_user_id,
            registered_at=now,
            archived_at=None,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def add_attachment(
        self,
        *,
        attachment_id: str,
        document_id: str,
        file_name: str,
        content_type: str | None,
        size_bytes: int,
        storage_key: str,
        attachment_kind: str,
        uploaded_by_user_id: int,
    ) -> CorrespondenceAttachmentModel:
        now = _utc_now()
        row = CorrespondenceAttachmentModel(
            id=attachment_id,
            document_id=document_id,
            file_name=file_name,
            content_type=content_type,
            size_bytes=size_bytes,
            storage_key=storage_key,
            attachment_kind=attachment_kind,
            uploaded_by_user_id=uploaded_by_user_id,
            created_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_id(self, document_id: str, *, load_attachments: bool = False) -> CorrespondenceDocumentModel | None:
        q = select(CorrespondenceDocumentModel).where(CorrespondenceDocumentModel.id == document_id)
        if load_attachments:
            q = q.options(selectinload(CorrespondenceDocumentModel.attachments))
        r = await self._session.execute(q)
        return r.scalars().one_or_none()

    async def list_documents(
        self,
        *,
        direction: str | None,
        statuses: list[str] | None,
        doc_types: list[str] | None,
        search: str | None,
        include_archived: bool,
        skip: int,
        limit: int,
    ) -> tuple[list[CorrespondenceDocumentModel], int]:
        conds: list[Any] = []
        if direction:
            conds.append(CorrespondenceDocumentModel.direction == direction)
        if not include_archived:
            conds.append(CorrespondenceDocumentModel.archived_at.is_(None))
        if statuses:
            conds.append(CorrespondenceDocumentModel.status.in_(statuses))
        if doc_types:
            conds.append(CorrespondenceDocumentModel.doc_type.in_(doc_types))
        if search and search.strip():
            qpat = f"%{search.strip()}%"
            conds.append(
                or_(
                    CorrespondenceDocumentModel.counterparty.ilike(qpat),
                    CorrespondenceDocumentModel.subject.ilike(qpat),
                    CorrespondenceDocumentModel.registry_number.ilike(qpat),
                )
            )
        where = and_(*conds) if conds else True
        cnt_q = select(func.count()).select_from(CorrespondenceDocumentModel).where(where)
        total = int((await self._session.execute(cnt_q)).scalar() or 0)
        q = (
            select(CorrespondenceDocumentModel)
            .where(where)
            .order_by(CorrespondenceDocumentModel.registered_at.desc(), CorrespondenceDocumentModel.created_at.desc())
            .offset(skip)
            .limit(limit)
            .options(selectinload(CorrespondenceDocumentModel.attachments))
        )
        rows = (await self._session.execute(q)).scalars().all()
        return list(rows), total

    async def get_stats(self) -> dict[str, int]:
        base = CorrespondenceDocumentModel.archived_at.is_(None)
        async def _count(*extra) -> int:
            conds = [base, *extra]
            q = select(func.count()).select_from(CorrespondenceDocumentModel).where(and_(*conds))
            return int((await self._session.execute(q)).scalar() or 0)

        return {
            "incoming_total": await _count(CorrespondenceDocumentModel.direction == "incoming"),
            "outgoing_total": await _count(CorrespondenceDocumentModel.direction == "outgoing"),
            "approval_total": await _count(CorrespondenceDocumentModel.status == "approval"),
            "incoming_new_total": await _count(
                CorrespondenceDocumentModel.direction == "incoming",
                CorrespondenceDocumentModel.status == "new",
            ),
        }

    async def update_document(
        self,
        row: CorrespondenceDocumentModel,
        *,
        status: str | None = None,
        responsible_user_id: int | None = None,
        comment: str | None = None,
    ) -> None:
        if status is not None:
            row.status = status
        if responsible_user_id is not None:
            row.responsible_user_id = responsible_user_id
        if comment is not None:
            row.comment = comment or None
        row.updated_at = _utc_now()

    async def archive_document(self, row: CorrespondenceDocumentModel) -> None:
        row.archived_at = _utc_now()
        row.updated_at = _utc_now()

    async def delete_attachment(self, att: CorrespondenceAttachmentModel) -> None:
        await self._session.delete(att)

    async def count_attachments_by_kind(self, document_id: str, kind: str) -> int:
        q = (
            select(func.count())
            .select_from(CorrespondenceAttachmentModel)
            .where(
                CorrespondenceAttachmentModel.document_id == document_id,
                CorrespondenceAttachmentModel.attachment_kind == kind,
            )
        )
        return int((await self._session.execute(q)).scalar() or 0)
