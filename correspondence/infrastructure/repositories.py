from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from application.correspondence_service import format_registry_number
from infrastructure.models import (
    CorrespondenceAttachmentModel,
    CorrespondenceDocumentCommentModel,
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
        registry_number: str | None,
        direction: str,
        doc_type: str,
        status: str,
        counterparty: str,
        subject: str,
        comment: str | None,
        partner_user_id: int | None,
        responsible_user_id: int,
        registered_at: datetime | None = None,
        rejection_comment: str | None = None,
    ) -> CorrespondenceDocumentModel:
        now = _utc_now()
        row = CorrespondenceDocumentModel(
            id=id_,
            registry_number=registry_number,
            direction=direction,
            doc_type=doc_type,
            status=status,
            counterparty=counterparty.strip(),
            subject=subject.strip(),
            comment=(comment or None),
            rejection_comment=(rejection_comment or None),
            partner_user_id=partner_user_id,
            responsible_user_id=responsible_user_id,
            registered_at=registered_at,
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
        registered_only: bool = False,
        partner_user_id: int | None = None,
    ) -> tuple[list[CorrespondenceDocumentModel], int]:
        conds: list[Any] = []
        if direction:
            conds.append(CorrespondenceDocumentModel.direction == direction)
        if not include_archived:
            conds.append(CorrespondenceDocumentModel.archived_at.is_(None))
        if registered_only:
            conds.append(CorrespondenceDocumentModel.registry_number.is_not(None))
        if statuses:
            conds.append(CorrespondenceDocumentModel.status.in_(statuses))
        if doc_types:
            conds.append(CorrespondenceDocumentModel.doc_type.in_(doc_types))
        if partner_user_id is not None and partner_user_id > 0:
            conds.append(CorrespondenceDocumentModel.partner_user_id == int(partner_user_id))
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
        order_ts = func.coalesce(
            CorrespondenceDocumentModel.registered_at,
            CorrespondenceDocumentModel.created_at,
        )
        q = (
            select(CorrespondenceDocumentModel)
            .where(where)
            .order_by(order_ts.desc(), CorrespondenceDocumentModel.created_at.desc())
            .offset(skip)
            .limit(limit)
            .options(selectinload(CorrespondenceDocumentModel.attachments))
        )
        rows = (await self._session.execute(q)).scalars().all()
        return list(rows), total

    async def count_partner_attention_split(self, partner_user_id: int) -> tuple[int, int]:
        """(outgoing pending_review, incoming new) assigned to this partner."""
        if partner_user_id <= 0:
            return (0, 0)
        base = CorrespondenceDocumentModel.archived_at.is_(None)
        assigned = CorrespondenceDocumentModel.partner_user_id == int(partner_user_id)

        async def _count(*extra) -> int:
            q = select(func.count()).select_from(CorrespondenceDocumentModel).where(
                and_(base, assigned, *extra)
            )
            return int((await self._session.execute(q)).scalar() or 0)

        outgoing = await _count(
            CorrespondenceDocumentModel.direction == "outgoing",
            CorrespondenceDocumentModel.status == "pending_review",
        )
        incoming = await _count(
            CorrespondenceDocumentModel.direction == "incoming",
            CorrespondenceDocumentModel.status == "new",
        )
        return (outgoing, incoming)

    async def count_partner_attention(self, partner_user_id: int) -> int:
        outgoing, incoming = await self.count_partner_attention_split(partner_user_id)
        return outgoing + incoming

    async def get_stats(self, *, partner_user_id: int | None = None) -> dict[str, int]:
        base = CorrespondenceDocumentModel.archived_at.is_(None)

        async def _count(*extra) -> int:
            conds = [base, *extra]
            q = select(func.count()).select_from(CorrespondenceDocumentModel).where(and_(*conds))
            return int((await self._session.execute(q)).scalar() or 0)

        partner_outgoing_pending = 0
        partner_incoming_new = 0
        if partner_user_id is not None and partner_user_id > 0:
            partner_outgoing_pending, partner_incoming_new = await self.count_partner_attention_split(
                partner_user_id
            )

        return {
            "incoming_total": await _count(CorrespondenceDocumentModel.direction == "incoming"),
            "outgoing_total": await _count(
                CorrespondenceDocumentModel.direction == "outgoing",
                CorrespondenceDocumentModel.registry_number.is_not(None),
            ),
            "approval_total": await _count(
                CorrespondenceDocumentModel.status.in_(("approval", "pending_review")),
            ),
            "incoming_new_total": await _count(
                CorrespondenceDocumentModel.direction == "incoming",
                CorrespondenceDocumentModel.status == "new",
            ),
            "partner_attention_total": partner_outgoing_pending + partner_incoming_new,
            "partner_outgoing_pending": partner_outgoing_pending,
            "partner_incoming_new": partner_incoming_new,
        }

    async def update_document(
        self,
        row: CorrespondenceDocumentModel,
        *,
        status: str | None = None,
        responsible_user_id: int | None = None,
        comment: str | None = None,
        counterparty: str | None = None,
        subject: str | None = None,
        partner_user_id: int | None = None,
        clear_partner: bool = False,
        rejection_comment: str | None = None,
        clear_rejection_comment: bool = False,
        registry_number: str | None = None,
        registered_at: datetime | None = None,
        set_registered_at: bool = False,
    ) -> None:
        if status is not None:
            row.status = status
        if responsible_user_id is not None:
            row.responsible_user_id = responsible_user_id
        if comment is not None:
            row.comment = comment or None
        if counterparty is not None:
            row.counterparty = counterparty.strip()
        if subject is not None:
            row.subject = subject.strip()
        if clear_partner:
            row.partner_user_id = None
        elif partner_user_id is not None:
            row.partner_user_id = partner_user_id
        if clear_rejection_comment:
            row.rejection_comment = None
        elif rejection_comment is not None:
            row.rejection_comment = rejection_comment or None
        if registry_number is not None:
            row.registry_number = registry_number
        if set_registered_at:
            row.registered_at = registered_at
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

    async def list_comments(self, document_id: str) -> list[CorrespondenceDocumentCommentModel]:
        q = (
            select(CorrespondenceDocumentCommentModel)
            .where(CorrespondenceDocumentCommentModel.document_id == document_id)
            .order_by(
                CorrespondenceDocumentCommentModel.created_at.asc(),
                CorrespondenceDocumentCommentModel.id.asc(),
            )
        )
        return list((await self._session.execute(q)).scalars().all())

    async def add_comment(
        self,
        *,
        comment_id: str,
        document_id: str,
        author_user_id: int,
        body: str,
    ) -> CorrespondenceDocumentCommentModel:
        row = CorrespondenceDocumentCommentModel(
            id=comment_id,
            document_id=document_id,
            author_user_id=author_user_id,
            body=body,
            created_at=_utc_now(),
        )
        self._session.add(row)
        await self._session.flush()
        return row
