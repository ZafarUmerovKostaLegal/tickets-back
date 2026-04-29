from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from application.expense_service import REGISTRY_STATUSES

from infrastructure.models import (
    DepartmentModel,
    ExchangeRateModel,
    ExpenseAttachmentModel,
    ExpenseAuditLogModel,
    ExpenseKlSequenceModel,
    ExpenseRequestModel,
    ExpenseStatusHistoryModel,
    ExpenseTypeModel,
    ProjectModel,
)

_MISSING = object()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def next_kl_id(session: AsyncSession) -> str:
    r = await session.execute(
        select(ExpenseKlSequenceModel).where(ExpenseKlSequenceModel.singleton == 1).with_for_update()
    )
    row = r.scalars().one_or_none()
    if not row:
        row = ExpenseKlSequenceModel(singleton=1, last_seq=0)
        session.add(row)
        await session.flush()
    row.last_seq += 1
    await session.flush()
    return f"KL{row.last_seq:06d}"


class ExpenseRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, id_: str, *, load_children: bool = False) -> ExpenseRequestModel | None:
        q = select(ExpenseRequestModel).where(ExpenseRequestModel.id == id_)
        if load_children:
            q = q.options(
                selectinload(ExpenseRequestModel.attachments),
                selectinload(ExpenseRequestModel.status_history),
                selectinload(ExpenseRequestModel.audit_logs),
            )
        r = await self._session.execute(q)
        return r.scalars().one_or_none()

    async def count_attachments(self, expense_request_id: str) -> int:
        q = select(func.count()).select_from(ExpenseAttachmentModel).where(
            ExpenseAttachmentModel.expense_request_id == expense_request_id
        )
        r = await self._session.execute(q)
        return int(r.scalar() or 0)

    async def attachment_kind_metrics(self, expense_request_id: str) -> tuple[int, int, int]:

        q = select(ExpenseAttachmentModel).where(
            ExpenseAttachmentModel.expense_request_id == expense_request_id
        )
        r = await self._session.execute(q)
        rows = r.scalars().all()
        pd = pr = unt = 0
        for row in rows:
            k = (row.attachment_kind or "").strip()
            if k == "payment_document":
                pd += 1
            elif k == "payment_receipt":
                pr += 1
            else:
                unt += 1
        return pd, pr, unt

    async def list_requests(
        self,
        *,
        created_by_user_id: int | None,
        status: str | None,
        scope: str | None,
        expense_type: str | None,
        is_reimbursable: bool | None,
        date_from: date | None,
        date_to: date | None,
        department_id: str | None,
        project_id: str | None,
        search: str | None,
        sort_by: str,
        sort_order: str,
        skip: int,
        limit: int,
    ) -> tuple[list[ExpenseRequestModel], int]:
        q = select(ExpenseRequestModel)
        cnt = select(func.count()).select_from(ExpenseRequestModel)

        def _apply(stmt):
            if created_by_user_id is not None:
                stmt = stmt.where(ExpenseRequestModel.created_by_user_id == created_by_user_id)
            if scope == "registry":
                stmt = stmt.where(ExpenseRequestModel.status.in_(list(REGISTRY_STATUSES)))
            elif status:
                stmt = stmt.where(ExpenseRequestModel.status == status)
            if expense_type:
                stmt = stmt.where(ExpenseRequestModel.expense_type == expense_type)
            if is_reimbursable is not None:
                stmt = stmt.where(ExpenseRequestModel.is_reimbursable == is_reimbursable)
            if date_from:
                stmt = stmt.where(ExpenseRequestModel.expense_date >= date_from)
            if date_to:
                stmt = stmt.where(ExpenseRequestModel.expense_date <= date_to)
            if department_id:
                stmt = stmt.where(ExpenseRequestModel.department_id == department_id)
            if project_id:
                stmt = stmt.where(ExpenseRequestModel.project_id == project_id)
            if search and search.strip():
                s = f"%{search.strip()}%"
                stmt = stmt.where(
                    or_(
                        ExpenseRequestModel.id.ilike(s),
                        ExpenseRequestModel.description.ilike(s),
                        ExpenseRequestModel.vendor.ilike(s),
                    )
                )
            return stmt

        q = _apply(q)
        cnt = _apply(cnt)

        sort_map = {
            "createdAt": ExpenseRequestModel.created_at,
            "expenseDate": ExpenseRequestModel.expense_date,
            "amountUzs": ExpenseRequestModel.amount_uzs,
            "updatedAt": ExpenseRequestModel.updated_at,
            "status": ExpenseRequestModel.status,
        }
        order_col = sort_map.get(sort_by, ExpenseRequestModel.created_at)
        if sort_order == "asc":
            q = q.order_by(order_col.asc())
        else:
            q = q.order_by(order_col.desc())

        q = q.offset(skip).limit(limit).options(selectinload(ExpenseRequestModel.attachments))
        rows = await self._session.execute(q)
        total_r = await self._session.execute(cnt)
        return list(rows.scalars().all()), int(total_r.scalar() or 0)

    async def create(
        self,
        *,
        id_: str,
        description: str,
        expense_date: date,
        payment_deadline: date | None,
        amount_uzs: Decimal,
        exchange_rate: Decimal,
        equivalent_amount: Decimal,
        expense_type: str,
        expense_subtype: str | None,
        is_reimbursable: bool,
        payment_method: str | None,
        department_id: str | None,
        project_id: str | None,
        expense_category_id: str | None = None,
        vendor: str | None = None,
        business_purpose: str | None = None,
        comment: str | None = None,
        status: str = "draft",
        created_by_user_id: int = 0,
        updated_by_user_id: int = 0,
    ) -> ExpenseRequestModel:
        now = _now_utc()
        row = ExpenseRequestModel(
            id=id_,
            description=description.strip(),
            expense_date=expense_date,
            payment_deadline=payment_deadline,
            amount_uzs=amount_uzs,
            exchange_rate=exchange_rate,
            equivalent_amount=equivalent_amount,
            expense_type=expense_type.strip(),
            expense_subtype=(expense_subtype or None),
            is_reimbursable=is_reimbursable,
            payment_method=payment_method,
            department_id=department_id,
            project_id=project_id,
            expense_category_id=expense_category_id,
            vendor=vendor,
            business_purpose=business_purpose,
            comment=comment,
            status=status,
            created_by_user_id=created_by_user_id,
            updated_by_user_id=updated_by_user_id,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def update_fields(
        self,
        row: ExpenseRequestModel,
        *,
        description: str | None,
        expense_date: date | None,
        payment_deadline: date | None | object = _MISSING,
        amount_uzs: Decimal | None,
        exchange_rate: Decimal | None,
        equivalent_amount: Decimal | None,
        expense_type: str | None,
        expense_subtype: str | None,
        is_reimbursable: bool | None,
        payment_method: str | None,
        department_id: str | None,
        project_id: str | None,
        expense_category_id: str | None = None,
        vendor: str | None = None,
        business_purpose: str | None = None,
        comment: str | None = None,
        current_approver_id: int | None = None,
        updated_by_user_id: int = 0,
    ) -> None:
        if description is not None:
            row.description = description.strip()
        if expense_date is not None:
            row.expense_date = expense_date
        if payment_deadline is not _MISSING:
            row.payment_deadline = payment_deadline
        if amount_uzs is not None:
            row.amount_uzs = amount_uzs
        if exchange_rate is not None:
            row.exchange_rate = exchange_rate
        if equivalent_amount is not None:
            row.equivalent_amount = equivalent_amount
        if expense_type is not None:
            row.expense_type = expense_type.strip()
        if expense_subtype is not None:
            row.expense_subtype = expense_subtype
        if is_reimbursable is not None:
            row.is_reimbursable = is_reimbursable
        if payment_method is not None:
            row.payment_method = payment_method
        if department_id is not None:
            row.department_id = department_id
        if project_id is not None:
            row.project_id = project_id
        if expense_category_id is not None:
            row.expense_category_id = expense_category_id
        if vendor is not None:
            row.vendor = vendor
        if business_purpose is not None:
            row.business_purpose = business_purpose
        if comment is not None:
            row.comment = comment
        if current_approver_id is not None:
            row.current_approver_id = current_approver_id
        row.updated_by_user_id = updated_by_user_id
        row.updated_at = _now_utc()
        self._session.add(row)

    async def add_audit(
        self,
        *,
        expense_request_id: str,
        action: str,
        field_name: str | None,
        old_value: str | None,
        new_value: str | None,
        performed_by_user_id: int,
    ) -> None:
        log = ExpenseAuditLogModel(
            expense_request_id=expense_request_id,
            action=action,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            performed_by_user_id=performed_by_user_id,
            performed_at=_now_utc(),
        )
        self._session.add(log)

    async def add_status_history(
        self,
        *,
        expense_request_id: str,
        from_status: str | None,
        to_status: str,
        changed_by_user_id: int,
        comment: str | None,
    ) -> None:
        h = ExpenseStatusHistoryModel(
            expense_request_id=expense_request_id,
            from_status=from_status,
            to_status=to_status,
            changed_by_user_id=changed_by_user_id,
            comment=comment,
            changed_at=_now_utc(),
        )
        self._session.add(h)

    async def add_attachment(
        self,
        *,
        attachment_id: str,
        expense_request_id: str,
        file_name: str,
        storage_key: str,
        mime_type: str | None,
        size_bytes: int,
        uploaded_by_user_id: int,
        attachment_kind: str | None = None,
    ) -> ExpenseAttachmentModel:
        att = ExpenseAttachmentModel(
            id=attachment_id,
            expense_request_id=expense_request_id,
            file_name=file_name,
            storage_key=storage_key,
            mime_type=mime_type,
            size_bytes=size_bytes,
            attachment_kind=attachment_kind,
            uploaded_by_user_id=uploaded_by_user_id,
            uploaded_at=_now_utc(),
        )
        self._session.add(att)
        await self._session.flush()
        return att

    async def delete_attachment(self, expense_request_id: str, attachment_id: str) -> bool:
        r = await self._session.execute(
            select(ExpenseAttachmentModel).where(
                and_(
                    ExpenseAttachmentModel.id == attachment_id,
                    ExpenseAttachmentModel.expense_request_id == expense_request_id,
                )
            )
        )
        row = r.scalars().one_or_none()
        if not row:
            return False
        await self._session.delete(row)
        return True

    async def health_check(self) -> bool:
        try:
            await self._session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False


    async def aggregate_expenses_for_project(
        self,
        project_id: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict:

        from application.expense_service import REGISTRY_STATUSES

        statuses = tuple(REGISTRY_STATUSES)
        from sqlalchemy import and_

        conds = [
            ExpenseRequestModel.project_id == project_id,
            ExpenseRequestModel.status.in_(list(statuses)),
        ]
        if date_from is not None:
            conds.append(ExpenseRequestModel.expense_date >= date_from)
        if date_to is not None:
            conds.append(ExpenseRequestModel.expense_date <= date_to)

        stmt = select(
            func.coalesce(func.sum(ExpenseRequestModel.amount_uzs), 0),
            func.count(),
        ).where(and_(*conds))
        r = await self._session.execute(stmt)
        total_uzs, count = r.one()
        return {"total_amount_uzs": float(total_uzs), "count": int(count)}

    async def list_for_report(
        self,
        *,
        date_from: date,
        date_to: date,
        user_ids: list[int] | None = None,
        project_ids: list[str] | None = None,
    ) -> list[ExpenseRequestModel]:

        from application.expense_service import REPORT_INCLUSION_STATUSES

        statuses = tuple(REPORT_INCLUSION_STATUSES)
        conds = [
            ExpenseRequestModel.status.in_(list(statuses)),
            ExpenseRequestModel.expense_date >= date_from,
            ExpenseRequestModel.expense_date <= date_to,
        ]
        if user_ids:
            conds.append(ExpenseRequestModel.created_by_user_id.in_(user_ids))
        if project_ids:
            conds.append(ExpenseRequestModel.project_id.in_(project_ids))

        conds.append(ExpenseRequestModel.project_id.isnot(None))
        conds.append(ExpenseRequestModel.project_id != "")
        q = (
            select(ExpenseRequestModel)
            .where(and_(*conds))
            .order_by(ExpenseRequestModel.expense_date.asc(), ExpenseRequestModel.created_at.asc())
        )
        return list((await self._session.execute(q)).scalars().all())

    async def list_expense_types(self) -> list[ExpenseTypeModel]:
        r = await self._session.execute(select(ExpenseTypeModel).order_by(ExpenseTypeModel.sort_order))
        return list(r.scalars().all())

    async def list_departments(self) -> list[DepartmentModel]:
        r = await self._session.execute(select(DepartmentModel).order_by(DepartmentModel.name))
        return list(r.scalars().all())

    async def list_projects(self) -> list[ProjectModel]:
        r = await self._session.execute(select(ProjectModel).order_by(ProjectModel.name))
        return list(r.scalars().all())

    async def get_exchange_rate_for_date(self, d: date) -> ExchangeRateModel | None:
        r = await self._session.execute(
            select(ExchangeRateModel)
            .where(ExchangeRateModel.rate_date <= d)
            .order_by(ExchangeRateModel.rate_date.desc())
            .limit(1)
        )
        return r.scalars().one_or_none()


async def seed_reference_data(session: AsyncSession) -> None:

    types_ = [
        ("transport", "Транспорт", 10),
        ("food", "Питание", 20),
        ("accommodation", "Проживание", 30),
        ("purchase", "Закупка", 40),
        ("services", "Услуги", 50),
        ("entertainment", "Развлечения", 60),
        ("client_expense", "Расход клиента", 70),
        ("partner_expense", "Партнёрский расход", 75),
        ("other", "Прочее", 100),
    ]
    for code, label, so in types_:
        ex = await session.get(ExpenseTypeModel, code)
        if not ex:
            session.add(ExpenseTypeModel(code=code, label=label, sort_order=so))

    depts = [("sales", "Продажи"), ("legal", "Юристы"), ("it", "IT")]
    for did, name in depts:
        if not await session.get(DepartmentModel, did):
            session.add(DepartmentModel(id=did, name=name))

    projs = [("project-a", "Проект A"), ("project-b", "Проект B")]
    for pid, name in projs:
        if not await session.get(ProjectModel, pid):
            session.add(ProjectModel(id=pid, name=name))


    r = await session.execute(select(ExchangeRateModel).where(ExchangeRateModel.rate_date == date.today()))
    if r.scalars().one_or_none() is None:
        session.add(
            ExchangeRateModel(
                rate_date=date.today(),
                rate=Decimal("12850.00"),
                pair_label="UZS/USD_equiv",
            )
        )
    await session.flush()
