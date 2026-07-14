
from __future__ import annotations

import json
import uuid
from datetime import date

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from infrastructure.models_reports import (
    ReportPartnerConfirmationCommentModel,
    ReportPartnerConfirmationRequestModel,
    ReportPartnerConfirmationSignatureModel,
    ReportSnapshotRowModel,
)
from infrastructure.repository_shared import _now_utc

_STATUS_PENDING = "pending_partners"
_STATUS_CONFIRMED = "fully_confirmed"
_DEFAULT_REVIEW_PRIORITY = "yellow"


def project_id_from_snapshot_row(row: ReportSnapshotRowModel) -> str | None:
    try:
        d = json.loads(row.frozen_data_json or "{}")
        if isinstance(d, dict):
            pid = d.get("projectId")
            if pid is not None and str(pid).strip():
                return str(pid).strip()
    except (json.JSONDecodeError, TypeError):
        pass
    st = (row.source_type or "").strip().lower()
    if st in ("project", "projects", "client_project"):
        sid = (row.source_id or "").strip()
        return sid or None
    return None


class PartnerReportConfirmationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def snapshot_has_project_row(self, snapshot_id: str, project_id: str) -> bool:
        pid = (project_id or "").strip()
        if not pid:
            return False
        q = select(ReportSnapshotRowModel).where(ReportSnapshotRowModel.snapshot_id == snapshot_id)
        rows = list((await self._s.execute(q)).scalars().all())
        for r in rows:
            rp = project_id_from_snapshot_row(r)
            if rp == pid:
                return True
        return False

    async def get_request_by_id(
        self, request_id: str, *, load_signatures: bool = False
    ) -> ReportPartnerConfirmationRequestModel | None:
        q = select(ReportPartnerConfirmationRequestModel).where(
            ReportPartnerConfirmationRequestModel.id == request_id
        )
        if load_signatures:
            q = q.options(selectinload(ReportPartnerConfirmationRequestModel.signatures))
        return (await self._s.execute(q)).scalars().one_or_none()

    async def find_latest_pending_for_project_period(
        self,
        project_id: str,
        date_from: date,
        date_to: date,
    ) -> ReportPartnerConfirmationRequestModel | None:
        pid = (project_id or "").strip()
        q = (
            select(ReportPartnerConfirmationRequestModel)
            .where(
                and_(
                    ReportPartnerConfirmationRequestModel.project_id == pid,
                    ReportPartnerConfirmationRequestModel.date_from == date_from,
                    ReportPartnerConfirmationRequestModel.date_to == date_to,
                    ReportPartnerConfirmationRequestModel.status == _STATUS_PENDING,
                )
            )
            .options(selectinload(ReportPartnerConfirmationRequestModel.signatures))
            .order_by(ReportPartnerConfirmationRequestModel.created_at.desc())
            .limit(1)
        )
        return (await self._s.execute(q)).scalars().one_or_none()

    async def upsert_submit(
        self,
        *,
        snapshot_id: str,
        project_id: str,
        date_from: date,
        date_to: date,
        title: str,
        submitted_by_auth_user_id: int,
    ) -> ReportPartnerConfirmationRequestModel:
        q = select(ReportPartnerConfirmationRequestModel).where(
            and_(
                ReportPartnerConfirmationRequestModel.snapshot_id == snapshot_id,
                ReportPartnerConfirmationRequestModel.project_id == project_id,
                ReportPartnerConfirmationRequestModel.date_from == date_from,
                ReportPartnerConfirmationRequestModel.date_to == date_to,
            )
        )
        row = (await self._s.execute(q)).scalars().one_or_none()
        now = _now_utc()
        if row:
            await self._s.execute(
                delete(ReportPartnerConfirmationSignatureModel).where(
                    ReportPartnerConfirmationSignatureModel.request_id == row.id
                )
            )
            row.status = _STATUS_PENDING
            row.review_priority = _DEFAULT_REVIEW_PRIORITY
            row.title = title
            row.submitted_by_auth_user_id = submitted_by_auth_user_id
            row.updated_at = now
            self._s.add(row)
            await self._s.flush()
            return row
        m = ReportPartnerConfirmationRequestModel(
            id=str(uuid.uuid4()),
            snapshot_id=snapshot_id,
            project_id=project_id,
            date_from=date_from,
            date_to=date_to,
            title=title,
            status=_STATUS_PENDING,
            review_priority=_DEFAULT_REVIEW_PRIORITY,
            submitted_by_auth_user_id=submitted_by_auth_user_id,
            created_at=now,
            updated_at=now,
        )
        self._s.add(m)
        await self._s.flush()
        return m

    async def invalidate_all_for_snapshot_project(self, snapshot_id: str, project_id: str) -> int:
        q = select(ReportPartnerConfirmationRequestModel.id).where(
            and_(
                ReportPartnerConfirmationRequestModel.snapshot_id == snapshot_id,
                ReportPartnerConfirmationRequestModel.project_id == project_id,
            )
        )
        ids = [str(x) for x in (await self._s.execute(q)).scalars().all()]
        if not ids:
            return 0
        await self._s.execute(
            delete(ReportPartnerConfirmationSignatureModel).where(
                ReportPartnerConfirmationSignatureModel.request_id.in_(ids)
            )
        )
        now = _now_utc()
        for rid in ids:
            row = await self.get_request_by_id(rid)
            if row:
                row.status = _STATUS_PENDING
                row.updated_at = now
                self._s.add(row)
        return len(ids)

    async def add_signature(
        self, request_id: str, partner_auth_user_id: int
    ) -> ReportPartnerConfirmationSignatureModel:
        now = _now_utc()
        sig = ReportPartnerConfirmationSignatureModel(
            id=str(uuid.uuid4()),
            request_id=request_id,
            partner_auth_user_id=partner_auth_user_id,
            confirmed_at=now,
        )
        self._s.add(sig)
        return sig

    async def list_signature_partner_ids(self, request_id: str) -> list[int]:
        q = select(ReportPartnerConfirmationSignatureModel.partner_auth_user_id).where(
            ReportPartnerConfirmationSignatureModel.request_id == request_id
        )
        return sorted({int(x) for x in (await self._s.execute(q)).scalars().all()})

    async def partner_has_signed(self, request_id: str, partner_auth_user_id: int) -> bool:
        q = select(ReportPartnerConfirmationSignatureModel.id).where(
            and_(
                ReportPartnerConfirmationSignatureModel.request_id == request_id,
                ReportPartnerConfirmationSignatureModel.partner_auth_user_id
                == int(partner_auth_user_id),
            )
        )
        return (await self._s.execute(q)).scalar_one_or_none() is not None

    async def remove_signature(self, request_id: str, partner_auth_user_id: int) -> bool:
        """Удаляет одну подпись партнёра SQL DELETE (без ORM-состояния в relationship)."""
        rid = (request_id or "").strip()
        if not rid:
            return False
        result = await self._s.execute(
            delete(ReportPartnerConfirmationSignatureModel).where(
                and_(
                    ReportPartnerConfirmationSignatureModel.request_id == rid,
                    ReportPartnerConfirmationSignatureModel.partner_auth_user_id
                    == int(partner_auth_user_id),
                )
            )
        )
        await self._s.flush()
        # Сбрасываем кэш relationship у заявки, если она уже была загружена с подписями.
        req = await self.get_request_by_id(rid, load_signatures=False)
        if req is not None:
            self._s.expire(req, ["signatures", "status", "updated_at"])
        return int(result.rowcount or 0) > 0

    async def mark_pending_partners(self, request_id: str) -> None:
        row = await self.get_request_by_id(request_id)
        if not row:
            return
        row.status = _STATUS_PENDING
        row.updated_at = _now_utc()
        self._s.add(row)

    async def set_review_priority(self, request_id: str, review_priority: str) -> bool:
        row = await self.get_request_by_id(request_id)
        if not row:
            return False
        row.review_priority = review_priority
        row.updated_at = _now_utc()
        self._s.add(row)
        return True

    async def mark_fully_confirmed(self, request_id: str) -> None:
        row = await self.get_request_by_id(request_id)
        if not row:
            return
        row.status = _STATUS_CONFIRMED
        row.updated_at = _now_utc()
        self._s.add(row)

    async def delete_request(self, request_id: str) -> bool:
        """Удаляет заявку и подписи (CASCADE). Возвращает False, если записи нет."""
        rid = (request_id or "").strip()
        if not rid:
            return False
        row = await self.get_request_by_id(rid, load_signatures=False)
        if not row:
            return False
        await self._s.delete(row)
        await self._s.flush()
        return True

    async def has_fully_confirmed_for_project_period(
        self,
        project_id: str,
        date_from: date,
        date_to: date,
    ) -> bool:
        """Есть ли полное подтверждение партнёров, период которого **целиком охватывает** [date_from, date_to].

        Раньше требовалось точное совпадение дат с записью подтверждения — из‑за этого счёт/unbilled
        блокировались, если UI передавал более узкий диапазон внутри уже подтверждённого месяца.
        """
        pid = (project_id or "").strip()
        if not pid or date_to < date_from:
            return False
        q = (
            select(func.count())
            .select_from(ReportPartnerConfirmationRequestModel)
            .where(
                and_(
                    ReportPartnerConfirmationRequestModel.project_id == pid,
                    ReportPartnerConfirmationRequestModel.status == _STATUS_CONFIRMED,
                    ReportPartnerConfirmationRequestModel.date_from <= date_from,
                    ReportPartnerConfirmationRequestModel.date_to >= date_to,
                )
            )
        )
        n = int((await self._s.execute(q)).scalar_one() or 0)
        return n > 0

    def _confirmed_period_filters(
        self,
        q,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        before: date | None = None,
    ):
        if before is not None:
            return q.where(ReportPartnerConfirmationRequestModel.date_to < before)
        if date_from is not None and date_to is not None:
            return q.where(
                and_(
                    ReportPartnerConfirmationRequestModel.date_from <= date_to,
                    ReportPartnerConfirmationRequestModel.date_to >= date_from,
                )
            )
        return q

    async def list_all_fully_confirmed(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        before: date | None = None,
        limit_to_project_ids: set[str] | None = None,
    ) -> list[ReportPartnerConfirmationRequestModel]:
        q = (
            select(ReportPartnerConfirmationRequestModel)
            .where(ReportPartnerConfirmationRequestModel.status == _STATUS_CONFIRMED)
            .options(selectinload(ReportPartnerConfirmationRequestModel.signatures))
            .order_by(ReportPartnerConfirmationRequestModel.updated_at.desc())
        )
        q = self._confirmed_period_filters(
            q, date_from=date_from, date_to=date_to, before=before
        )
        if limit_to_project_ids is not None:
            if not limit_to_project_ids:
                return []
            q = q.where(
                ReportPartnerConfirmationRequestModel.project_id.in_(limit_to_project_ids)
            )
        return list((await self._s.execute(q)).scalars().all())

    async def list_all_pending(
        self,
        *,
        review_priority: str | None = None,
    ) -> list[ReportPartnerConfirmationRequestModel]:
        q = (
            select(ReportPartnerConfirmationRequestModel)
            .where(ReportPartnerConfirmationRequestModel.status != _STATUS_CONFIRMED)
            .options(selectinload(ReportPartnerConfirmationRequestModel.signatures))
        )
        if review_priority:
            q = q.where(
                ReportPartnerConfirmationRequestModel.review_priority == review_priority
            )
        # Сортировка red → yellow → green, внутри — старше выше; точный порядок
        # после visibility-фильтра всё равно пересчитывается в сервисе.
        priority_rank = func.case(
            (ReportPartnerConfirmationRequestModel.review_priority == "red", 0),
            (ReportPartnerConfirmationRequestModel.review_priority == "yellow", 1),
            (ReportPartnerConfirmationRequestModel.review_priority == "green", 2),
            else_=1,
        )
        q = q.order_by(
            priority_rank.asc(),
            ReportPartnerConfirmationRequestModel.created_at.asc(),
            ReportPartnerConfirmationRequestModel.id.asc(),
        )
        return list((await self._s.execute(q)).scalars().all())

    async def list_pending_for_partner(
        self, partner_auth_user_id: int
    ) -> list[ReportPartnerConfirmationRequestModel]:
        q = (
            select(ReportPartnerConfirmationRequestModel)
            .where(ReportPartnerConfirmationRequestModel.status == _STATUS_PENDING)
            .options(selectinload(ReportPartnerConfirmationRequestModel.signatures))
            .order_by(ReportPartnerConfirmationRequestModel.updated_at.desc())
        )
        all_rows = list((await self._s.execute(q)).scalars().all())
        out: list[ReportPartnerConfirmationRequestModel] = []
        pid = int(partner_auth_user_id)
        for m in all_rows:
            signed = {s.partner_auth_user_id for s in (m.signatures or [])}
            if pid not in signed:
                out.append(m)
        return out

    async def list_confirmed_visible_for(
        self,
        viewer_id: int,
        *,
        partner_project_ids: set[str],
        date_from: date | None = None,
        date_to: date | None = None,
        before: date | None = None,
    ) -> list[ReportPartnerConfirmationRequestModel]:
        q = (
            select(ReportPartnerConfirmationRequestModel)
            .where(ReportPartnerConfirmationRequestModel.status == _STATUS_CONFIRMED)
            .options(selectinload(ReportPartnerConfirmationRequestModel.signatures))
            .order_by(ReportPartnerConfirmationRequestModel.updated_at.desc())
        )
        q = self._confirmed_period_filters(
            q, date_from=date_from, date_to=date_to, before=before
        )
        rows = list((await self._s.execute(q)).scalars().all())
        vid = int(viewer_id)
        out: list[ReportPartnerConfirmationRequestModel] = []
        for m in rows:
            if m.submitted_by_auth_user_id == vid:
                out.append(m)
                continue
            if m.project_id in partner_project_ids:
                out.append(m)
                continue
            signed_ids = {s.partner_auth_user_id for s in (m.signatures or [])}
            if vid in signed_ids:
                out.append(m)
                continue
        return out

    async def list_visible_for(
        self,
        viewer_id: int,
        *,
        partner_project_ids: set[str],
        statuses: set[str],
        date_from: date | None = None,
        date_to: date | None = None,
        before: date | None = None,
    ) -> list[ReportPartnerConfirmationRequestModel]:
        """Список подтверждений для вкладки «Подтверждённые партнёром».

        В отличие от list_confirmed_visible_for, может включать pending_partners,
        чтобы отчёт был виден сразу после первой подписи, но с подсказкой, кто
        ещё не подтвердил.
        """
        st = {str(x) for x in (statuses or set()) if str(x).strip()}
        if not st:
            return []
        q = (
            select(ReportPartnerConfirmationRequestModel)
            .where(ReportPartnerConfirmationRequestModel.status.in_(st))
            .options(selectinload(ReportPartnerConfirmationRequestModel.signatures))
            .order_by(ReportPartnerConfirmationRequestModel.updated_at.desc())
        )
        q = self._confirmed_period_filters(
            q, date_from=date_from, date_to=date_to, before=before
        )
        rows = list((await self._s.execute(q)).scalars().all())
        vid = int(viewer_id)
        out: list[ReportPartnerConfirmationRequestModel] = []
        for m in rows:
            if m.submitted_by_auth_user_id == vid:
                out.append(m)
                continue
            if m.project_id in partner_project_ids:
                out.append(m)
                continue
            signed_ids = {s.partner_auth_user_id for s in (m.signatures or [])}
            if vid in signed_ids:
                out.append(m)
                continue
        return out

    async def list_comments(
        self, request_id: str
    ) -> list[ReportPartnerConfirmationCommentModel]:
        q = (
            select(ReportPartnerConfirmationCommentModel)
            .where(ReportPartnerConfirmationCommentModel.request_id == request_id)
            .order_by(ReportPartnerConfirmationCommentModel.created_at.asc())
        )
        return list((await self._s.execute(q)).scalars().all())

    async def add_comment(
        self,
        *,
        request_id: str,
        auth_user_id: int,
        text: str,
    ) -> ReportPartnerConfirmationCommentModel:
        now = _now_utc()
        row = ReportPartnerConfirmationCommentModel(
            id=str(uuid.uuid4()),
            request_id=request_id,
            auth_user_id=int(auth_user_id),
            text=text,
            created_at=now,
            updated_at=None,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def comments_summary_by_request_ids(
        self, request_ids: list[str]
    ) -> dict[str, tuple[int, ReportPartnerConfirmationCommentModel | None]]:
        """Для списка confirmed: (count, last_comment) по request_id."""
        ids = [str(x).strip() for x in request_ids if str(x).strip()]
        if not ids:
            return {}
        count_q = (
            select(
                ReportPartnerConfirmationCommentModel.request_id,
                func.count().label("cnt"),
            )
            .where(ReportPartnerConfirmationCommentModel.request_id.in_(ids))
            .group_by(ReportPartnerConfirmationCommentModel.request_id)
        )
        counts = {
            str(rid): int(cnt or 0)
            for rid, cnt in (await self._s.execute(count_q)).all()
        }
        # Последний комментарий: max(created_at) per request
        last_subq = (
            select(
                ReportPartnerConfirmationCommentModel.request_id.label("rid"),
                func.max(ReportPartnerConfirmationCommentModel.created_at).label(
                    "max_created"
                ),
            )
            .where(ReportPartnerConfirmationCommentModel.request_id.in_(ids))
            .group_by(ReportPartnerConfirmationCommentModel.request_id)
            .subquery()
        )
        last_q = (
            select(ReportPartnerConfirmationCommentModel)
            .join(
                last_subq,
                and_(
                    ReportPartnerConfirmationCommentModel.request_id == last_subq.c.rid,
                    ReportPartnerConfirmationCommentModel.created_at
                    == last_subq.c.max_created,
                ),
            )
        )
        last_by_id: dict[str, ReportPartnerConfirmationCommentModel] = {}
        for row in (await self._s.execute(last_q)).scalars().all():
            last_by_id[str(row.request_id)] = row
        out: dict[str, tuple[int, ReportPartnerConfirmationCommentModel | None]] = {}
        for rid in ids:
            out[rid] = (counts.get(rid, 0), last_by_id.get(rid))
        return out
