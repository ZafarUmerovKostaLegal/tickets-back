from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from infrastructure.models import (
    TimeEntryModel,
    TimeManagerClientContactModel,
    TimeManagerClientExpenseCategoryModel,
    TimeManagerClientModel,
    TimeManagerClientProjectModel,
    TimeManagerClientTaskModel,
)
from infrastructure.repository_shared import (
    _PROJECT_TYPES,
    _REPORT_VISIBILITY,
    _decimal_none,
    _now_utc,
    _strip_opt,
)


class ClientRepository:


    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_all(self, *, include_archived: bool = False) -> list[TimeManagerClientModel]:
        q = select(TimeManagerClientModel)
        if not include_archived:
            q = q.where(TimeManagerClientModel.is_archived.is_(False))
        q = q.order_by(TimeManagerClientModel.name.asc())
        r = await self._session.execute(q)
        return list(r.scalars().all())

    async def count_all(self, *, include_archived: bool = False) -> int:
        q = select(func.count()).select_from(TimeManagerClientModel)
        if not include_archived:
            q = q.where(TimeManagerClientModel.is_archived.is_(False))
        n = (await self._session.execute(q)).scalar_one()
        return int(n or 0)

    async def list_all_paginated(
        self,
        *,
        include_archived: bool = False,
        limit: int,
        offset: int,
    ) -> tuple[list[TimeManagerClientModel], int]:
        total = await self.count_all(include_archived=include_archived)
        q = select(TimeManagerClientModel)
        if not include_archived:
            q = q.where(TimeManagerClientModel.is_archived.is_(False))
        q = q.order_by(TimeManagerClientModel.name.asc()).limit(limit).offset(offset)
        r = await self._session.execute(q)
        return list(r.scalars().all()), total

    async def get_by_ids(self, client_ids: set[str]) -> dict[str, TimeManagerClientModel]:
        if not client_ids:
            return {}
        r = await self._session.execute(
            select(TimeManagerClientModel).where(TimeManagerClientModel.id.in_(client_ids))
        )
        return {row.id: row for row in r.scalars().all()}

    async def get_by_id(self, client_id: str) -> TimeManagerClientModel | None:
        r = await self._session.execute(
            select(TimeManagerClientModel).where(TimeManagerClientModel.id == client_id)
        )
        return r.scalars().one_or_none()

    async def get_by_id_with_contacts(self, client_id: str) -> TimeManagerClientModel | None:
        r = await self._session.execute(
            select(TimeManagerClientModel)
            .options(selectinload(TimeManagerClientModel.extra_contacts))
            .where(TimeManagerClientModel.id == client_id)
        )
        return r.scalars().one_or_none()

    async def create(
        self,
        *,
        name: str,
        address: str | None,
        currency: str,
        invoice_due_mode: str,
        invoice_due_days_after_issue: int | None,
        tax_percent: Decimal | None,
        tax2_percent: Decimal | None,
        discount_percent: Decimal | None,
        phone: str | None = None,
        email: str | None = None,
        contact_name: str | None = None,
        contact_phone: str | None = None,
        contact_email: str | None = None,
        is_archived: bool = False,
    ) -> TimeManagerClientModel:
        row = TimeManagerClientModel(
            id=str(uuid.uuid4()),
            name=name.strip(),
            address=address,
            currency=(currency or "USD").strip().upper()[:10],
            invoice_due_mode=(invoice_due_mode or "custom").strip()[:50],
            invoice_due_days_after_issue=invoice_due_days_after_issue,
            tax_percent=tax_percent,
            tax2_percent=tax2_percent,
            discount_percent=discount_percent,
            phone=(phone or None) and str(phone).strip()[:64] or None,
            email=(email or None) and str(email).strip()[:320] or None,
            contact_name=(contact_name or None) and str(contact_name).strip()[:500] or None,
            contact_phone=(contact_phone or None) and str(contact_phone).strip()[:64] or None,
            contact_email=(contact_email or None) and str(contact_email).strip()[:320] or None,
            is_archived=bool(is_archived),
            created_at=_now_utc(),
            updated_at=None,
        )
        self._session.add(row)

        await self._session.flush()
        return row

    async def update(self, client_id: str, patch: dict[str, Any]) -> TimeManagerClientModel | None:
        row = await self.get_by_id(client_id)
        if not row:
            return None
        if "name" in patch and patch["name"] is not None:
            row.name = str(patch["name"]).strip()
        if "address" in patch:
            row.address = patch["address"]
        if "currency" in patch and patch["currency"] is not None:
            row.currency = str(patch["currency"]).strip().upper()[:10]
        if "invoice_due_mode" in patch and patch["invoice_due_mode"] is not None:
            row.invoice_due_mode = str(patch["invoice_due_mode"]).strip()[:50]
        if "invoice_due_days_after_issue" in patch:
            row.invoice_due_days_after_issue = patch["invoice_due_days_after_issue"]
        if "tax_percent" in patch:
            row.tax_percent = patch["tax_percent"]
        if "tax2_percent" in patch:
            row.tax2_percent = patch["tax2_percent"]
        if "discount_percent" in patch:
            row.discount_percent = patch["discount_percent"]
        if "phone" in patch:
            v = patch["phone"]
            row.phone = str(v).strip()[:64] if v is not None and str(v).strip() else None
        if "email" in patch:
            v = patch["email"]
            row.email = str(v).strip()[:320] if v is not None and str(v).strip() else None
        if "contact_name" in patch:
            v = patch["contact_name"]
            row.contact_name = str(v).strip()[:500] if v is not None and str(v).strip() else None
        if "contact_phone" in patch:
            v = patch["contact_phone"]
            row.contact_phone = str(v).strip()[:64] if v is not None and str(v).strip() else None
        if "contact_email" in patch:
            v = patch["contact_email"]
            row.contact_email = str(v).strip()[:320] if v is not None and str(v).strip() else None
        if "is_archived" in patch and patch["is_archived"] is not None:
            row.is_archived = bool(patch["is_archived"])
        row.updated_at = _now_utc()
        self._session.add(row)
        return row

    async def delete(self, client_id: str) -> bool:
        row = await self.get_by_id(client_id)
        if not row:
            return False
        await self._session.execute(delete(TimeManagerClientModel).where(TimeManagerClientModel.id == client_id))
        return True


class ClientContactRepository:


    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_for_client(self, client_id: str) -> list[TimeManagerClientContactModel]:
        q = (
            select(TimeManagerClientContactModel)
            .where(TimeManagerClientContactModel.client_id == client_id)
            .order_by(TimeManagerClientContactModel.sort_order.asc().nulls_last(), TimeManagerClientContactModel.name)
        )
        r = await self._session.execute(q)
        return list(r.scalars().all())

    async def get_by_id(self, client_id: str, contact_id: str) -> TimeManagerClientContactModel | None:
        r = await self._session.execute(
            select(TimeManagerClientContactModel).where(
                TimeManagerClientContactModel.client_id == client_id,
                TimeManagerClientContactModel.id == contact_id,
            )
        )
        return r.scalars().one_or_none()

    async def create(
        self,
        *,
        client_id: str,
        name: str,
        phone: str | None,
        email: str | None,
        sort_order: int | None,
    ) -> TimeManagerClientContactModel:
        row = TimeManagerClientContactModel(
            id=str(uuid.uuid4()),
            client_id=client_id,
            name=name.strip(),
            phone=(phone or None) and str(phone).strip()[:64] or None,
            email=(email or None) and str(email).strip()[:320] or None,
            sort_order=sort_order,
            created_at=_now_utc(),
            updated_at=None,
        )
        self._session.add(row)
        return row

    async def update(self, client_id: str, contact_id: str, patch: dict[str, Any]) -> TimeManagerClientContactModel | None:
        row = await self.get_by_id(client_id, contact_id)
        if not row:
            return None
        if "name" in patch and patch["name"] is not None:
            row.name = str(patch["name"]).strip()
        if "phone" in patch:
            v = patch["phone"]
            row.phone = str(v).strip()[:64] if v is not None and str(v).strip() else None
        if "email" in patch:
            v = patch["email"]
            row.email = str(v).strip()[:320] if v is not None and str(v).strip() else None
        if "sort_order" in patch:
            row.sort_order = patch["sort_order"]
        row.updated_at = _now_utc()
        self._session.add(row)
        return row

    async def delete(self, client_id: str, contact_id: str) -> bool:
        row = await self.get_by_id(client_id, contact_id)
        if not row:
            return False
        await self._session.execute(
            delete(TimeManagerClientContactModel).where(
                TimeManagerClientContactModel.client_id == client_id,
                TimeManagerClientContactModel.id == contact_id,
            )
        )
        return True


class ClientTaskRepository:


    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_for_client(self, client_id: str) -> list[TimeManagerClientTaskModel]:
        q = (
            select(TimeManagerClientTaskModel)
            .where(TimeManagerClientTaskModel.client_id == client_id)
            .order_by(TimeManagerClientTaskModel.name.asc())
        )
        r = await self._session.execute(q)
        return list(r.scalars().all())

    async def get_by_id(self, client_id: str, task_id: str) -> TimeManagerClientTaskModel | None:
        r = await self._session.execute(
            select(TimeManagerClientTaskModel).where(
                TimeManagerClientTaskModel.client_id == client_id,
                TimeManagerClientTaskModel.id == task_id,
            )
        )
        return r.scalars().one_or_none()

    async def create(
        self,
        *,
        client_id: str,
        name: str,
        default_billable_rate: Decimal | None,
        billable_by_default: bool,
        common_for_future_projects: bool,
        add_to_existing_projects: bool,
    ) -> TimeManagerClientTaskModel:
        row = TimeManagerClientTaskModel(
            id=str(uuid.uuid4()),
            client_id=client_id,
            name=name.strip(),
            default_billable_rate=default_billable_rate,
            billable_by_default=billable_by_default,
            common_for_future_projects=common_for_future_projects,
            add_to_existing_projects=add_to_existing_projects,
            created_at=_now_utc(),
            updated_at=None,
        )
        self._session.add(row)
        return row

    async def update(self, client_id: str, task_id: str, patch: dict[str, Any]) -> TimeManagerClientTaskModel | None:
        row = await self.get_by_id(client_id, task_id)
        if not row:
            return None
        if "name" in patch and patch["name"] is not None:
            row.name = str(patch["name"]).strip()
        if "default_billable_rate" in patch:
            v = patch["default_billable_rate"]
            row.default_billable_rate = None if v is None else _decimal_none(v)
        if "billable_by_default" in patch:
            row.billable_by_default = bool(patch["billable_by_default"])
        if "common_for_future_projects" in patch:
            row.common_for_future_projects = bool(patch["common_for_future_projects"])
        if "add_to_existing_projects" in patch:
            row.add_to_existing_projects = bool(patch["add_to_existing_projects"])
        row.updated_at = _now_utc()
        self._session.add(row)
        return row

    async def delete(self, client_id: str, task_id: str) -> bool:
        row = await self.get_by_id(client_id, task_id)
        if not row:
            return False
        await self._session.execute(
            delete(TimeManagerClientTaskModel).where(
                TimeManagerClientTaskModel.client_id == client_id,
                TimeManagerClientTaskModel.id == task_id,
            )
        )
        return True


class ClientExpenseCategoryRepository:


    def __init__(self, session: AsyncSession):
        self._session = session

    @staticmethod
    def _normalize_name(name: str) -> str:
        return name.strip()

    async def list_for_client(
        self,
        client_id: str,
        *,
        include_archived: bool = False,
    ) -> list[TimeManagerClientExpenseCategoryModel]:
        q = select(TimeManagerClientExpenseCategoryModel).where(
            TimeManagerClientExpenseCategoryModel.client_id == client_id,
        )
        if not include_archived:
            q = q.where(TimeManagerClientExpenseCategoryModel.is_archived.is_(False))
        q = q.order_by(
            TimeManagerClientExpenseCategoryModel.sort_order.asc().nulls_last(),
            TimeManagerClientExpenseCategoryModel.name.asc(),
        )
        r = await self._session.execute(q)
        return list(r.scalars().all())

    async def get_by_id(
        self,
        client_id: str,
        category_id: str,
    ) -> TimeManagerClientExpenseCategoryModel | None:
        r = await self._session.execute(
            select(TimeManagerClientExpenseCategoryModel).where(
                TimeManagerClientExpenseCategoryModel.client_id == client_id,
                TimeManagerClientExpenseCategoryModel.id == category_id,
            )
        )
        return r.scalars().one_or_none()

    async def has_active_name_conflict(
        self,
        client_id: str,
        name: str,
        *,
        exclude_category_id: str | None = None,
    ) -> bool:
        norm = self._normalize_name(name).lower()
        if not norm:
            return False
        cond = and_(
            TimeManagerClientExpenseCategoryModel.client_id == client_id,
            TimeManagerClientExpenseCategoryModel.is_archived.is_(False),
            func.lower(func.trim(TimeManagerClientExpenseCategoryModel.name)) == norm,
        )
        if exclude_category_id:
            cond = and_(cond, TimeManagerClientExpenseCategoryModel.id != exclude_category_id)
        q = select(func.count()).select_from(TimeManagerClientExpenseCategoryModel).where(cond)
        n = (await self._session.execute(q)).scalar_one()
        return int(n or 0) > 0

    async def usage_count(self, category_id: str) -> int:
        _ = category_id
        return 0

    async def create(
        self,
        *,
        client_id: str,
        name: str,
        has_unit_price: bool = False,
        sort_order: int | None = None,
    ) -> TimeManagerClientExpenseCategoryModel:
        row = TimeManagerClientExpenseCategoryModel(
            id=str(uuid.uuid4()),
            client_id=client_id,
            name=self._normalize_name(name),
            has_unit_price=has_unit_price,
            is_archived=False,
            sort_order=sort_order,
            created_at=_now_utc(),
            updated_at=None,
        )
        self._session.add(row)
        return row

    async def update(
        self,
        client_id: str,
        category_id: str,
        patch: dict[str, Any],
    ) -> TimeManagerClientExpenseCategoryModel | None:
        row = await self.get_by_id(client_id, category_id)
        if not row:
            return None
        if "name" in patch and patch["name"] is not None:
            row.name = self._normalize_name(str(patch["name"]))
        if "has_unit_price" in patch:
            row.has_unit_price = bool(patch["has_unit_price"])
        if "is_archived" in patch:
            row.is_archived = bool(patch["is_archived"])
        if "sort_order" in patch:
            v = patch["sort_order"]
            row.sort_order = None if v is None else int(v)
        row.updated_at = _now_utc()
        self._session.add(row)
        return row

    async def delete(self, client_id: str, category_id: str) -> bool:
        row = await self.get_by_id(client_id, category_id)
        if not row:
            return False
        await self._session.execute(
            delete(TimeManagerClientExpenseCategoryModel).where(
                TimeManagerClientExpenseCategoryModel.client_id == client_id,
                TimeManagerClientExpenseCategoryModel.id == category_id,
            )
        )
        return True


class ClientProjectRepository:


    def __init__(self, session: AsyncSession):
        self._session = session

    @staticmethod
    def _normalize_code(code: str | None) -> str | None:
        return _strip_opt(code)

    async def list_for_client(
        self,
        client_id: str,
        *,
        include_archived: bool = False,
    ) -> list[TimeManagerClientProjectModel]:
        q = select(TimeManagerClientProjectModel).where(
            TimeManagerClientProjectModel.client_id == client_id,
        )
        if not include_archived:
            q = q.where(TimeManagerClientProjectModel.is_archived.is_(False))
        q = q.order_by(TimeManagerClientProjectModel.name.asc())
        r = await self._session.execute(q)
        return list(r.scalars().all())

    async def count_for_client(
        self,
        client_id: str,
        *,
        include_archived: bool = False,
    ) -> int:
        q = select(func.count()).select_from(TimeManagerClientProjectModel).where(
            TimeManagerClientProjectModel.client_id == client_id,
        )
        if not include_archived:
            q = q.where(TimeManagerClientProjectModel.is_archived.is_(False))
        n = (await self._session.execute(q)).scalar_one()
        return int(n or 0)

    async def list_for_client_paginated(
        self,
        client_id: str,
        *,
        include_archived: bool = False,
        limit: int,
        offset: int,
    ) -> tuple[list[TimeManagerClientProjectModel], int]:
        total = await self.count_for_client(client_id, include_archived=include_archived)
        q = select(TimeManagerClientProjectModel).where(
            TimeManagerClientProjectModel.client_id == client_id,
        )
        if not include_archived:
            q = q.where(TimeManagerClientProjectModel.is_archived.is_(False))
        q = q.order_by(TimeManagerClientProjectModel.name.asc()).limit(limit).offset(offset)
        r = await self._session.execute(q)
        return list(r.scalars().all()), total

    async def list_all_global(
        self,
        *,
        include_archived: bool = False,
    ) -> list[TimeManagerClientProjectModel]:
        q = select(TimeManagerClientProjectModel)
        if not include_archived:
            q = q.where(TimeManagerClientProjectModel.is_archived.is_(False))
        q = q.order_by(TimeManagerClientProjectModel.name.asc())
        r = await self._session.execute(q)
        return list(r.scalars().all())

    async def count_all_global(self, *, include_archived: bool = False) -> int:
        q = select(func.count()).select_from(TimeManagerClientProjectModel)
        if not include_archived:
            q = q.where(TimeManagerClientProjectModel.is_archived.is_(False))
        n = (await self._session.execute(q)).scalar_one()
        return int(n or 0)

    async def list_all_global_paginated(
        self,
        *,
        include_archived: bool = False,
        limit: int,
        offset: int,
    ) -> tuple[list[TimeManagerClientProjectModel], int]:
        total = await self.count_all_global(include_archived=include_archived)
        q = select(TimeManagerClientProjectModel)
        if not include_archived:
            q = q.where(TimeManagerClientProjectModel.is_archived.is_(False))
        q = q.order_by(TimeManagerClientProjectModel.name.asc()).limit(limit).offset(offset)
        r = await self._session.execute(q)
        return list(r.scalars().all()), total

    async def get_by_id(
        self,
        client_id: str,
        project_id: str,
    ) -> TimeManagerClientProjectModel | None:
        r = await self._session.execute(
            select(TimeManagerClientProjectModel).where(
                TimeManagerClientProjectModel.client_id == client_id,
                TimeManagerClientProjectModel.id == project_id,
            )
        )
        return r.scalars().one_or_none()

    async def get_by_id_global(self, project_id: str) -> TimeManagerClientProjectModel | None:
        r = await self._session.execute(
            select(TimeManagerClientProjectModel).where(TimeManagerClientProjectModel.id == project_id)
        )
        return r.scalars().one_or_none()

    async def get_last_project_with_code(self, client_id: str) -> TimeManagerClientProjectModel | None:
        q = (
            select(TimeManagerClientProjectModel)
            .where(
                TimeManagerClientProjectModel.client_id == client_id,
                TimeManagerClientProjectModel.code.isnot(None),
                func.trim(TimeManagerClientProjectModel.code) != "",
            )
            .order_by(
                TimeManagerClientProjectModel.updated_at.desc().nulls_last(),
                TimeManagerClientProjectModel.created_at.desc(),
            )
            .limit(1)
        )
        r = await self._session.execute(q)
        return r.scalars().one_or_none()

    async def has_code_conflict(
        self,
        client_id: str,
        code: str | None,
        *,
        exclude_project_id: str | None = None,
    ) -> bool:
        norm = self._normalize_code(code)
        if not norm:
            return False
        cond = and_(
            TimeManagerClientProjectModel.client_id == client_id,
            func.lower(func.trim(TimeManagerClientProjectModel.code)) == norm.lower(),
        )
        if exclude_project_id:
            cond = and_(cond, TimeManagerClientProjectModel.id != exclude_project_id)
        q = select(func.count()).select_from(TimeManagerClientProjectModel).where(cond)
        n = (await self._session.execute(q)).scalar_one()
        return int(n or 0) > 0

    async def allocate_duplicate_code(self, client_id: str, base: str | None) -> str | None:
        norm = self._normalize_code(base)
        if not norm:
            return None
        for i in range(0, 200):
            suffix = "-copy" if i == 0 else f"-copy-{i + 1}"
            max_prefix = max(1, 64 - len(suffix))
            cand = f"{norm[:max_prefix]}{suffix}"
            if not await self.has_code_conflict(client_id, cand):
                return cand
        return None

    @staticmethod
    def _name_with_copy_suffix(name: str) -> str:
        suffix = " (копия)"
        n = name.strip()
        if len(n) + len(suffix) <= 500:
            return n + suffix
        return n[: 500 - len(suffix)] + suffix

    async def duplicate_from(
        self,
        client_id: str,
        project_id: str,
    ) -> TimeManagerClientProjectModel | None:
        src = await self.get_by_id(client_id, project_id)
        if not src:
            return None
        new_code = await self.allocate_duplicate_code(client_id, src.code)
        return await self.create(
            client_id=client_id,
            name=self._name_with_copy_suffix(src.name),
            code=new_code,
            start_date=src.start_date,
            end_date=src.end_date,
            notes=src.notes,
            report_visibility=src.report_visibility,
            project_type=src.project_type,
            currency=src.currency,
            billable_rate_type=src.billable_rate_type,
            project_billable_rate_amount=src.project_billable_rate_amount,
            budget_type=src.budget_type,
            budget_amount=src.budget_amount,
            budget_hours=src.budget_hours,
            budget_resets_every_month=src.budget_resets_every_month,
            budget_includes_expenses=src.budget_includes_expenses,
            send_budget_alerts=src.send_budget_alerts,
            budget_alert_threshold_percent=src.budget_alert_threshold_percent,
            fixed_fee_amount=src.fixed_fee_amount,
            is_archived=False,
        )

    async def time_entries_count(self, project_id: str) -> int:
        q = select(func.count()).select_from(TimeEntryModel).where(
            TimeEntryModel.project_id == project_id,
            TimeEntryModel.voided_at.is_(None),
        )
        n = (await self._session.execute(q)).scalar_one()
        return int(n or 0)

    async def time_entries_counts_by_project_ids(self, project_ids: list[str]) -> dict[str, int]:
        unique = list(dict.fromkeys(project_ids))
        if not unique:
            return {}
        q = (
            select(TimeEntryModel.project_id, func.count())
            .where(
                TimeEntryModel.project_id.in_(unique),
                TimeEntryModel.voided_at.is_(None),
            )
            .group_by(TimeEntryModel.project_id)
        )
        r = await self._session.execute(q)
        out: dict[str, int] = {pid: 0 for pid in unique}
        for row in r.all():
            out[str(row[0])] = int(row[1] or 0)
        return out

    async def create(
        self,
        *,
        client_id: str,
        name: str,
        code: str | None,
        start_date: date | None,
        end_date: date | None,
        notes: str | None,
        report_visibility: str,
        project_type: str = "time_and_materials",
        currency: str = "USD",
        billable_rate_type: str | None = None,
        project_billable_rate_amount: Decimal | None = None,
        budget_type: str | None = None,
        budget_amount: Decimal | None = None,
        budget_hours: Decimal | None = None,
        budget_resets_every_month: bool = False,
        budget_includes_expenses: bool = False,
        send_budget_alerts: bool = False,
        budget_alert_threshold_percent: Decimal | None = None,
        fixed_fee_amount: Decimal | None = None,
        is_archived: bool = False,
    ) -> TimeManagerClientProjectModel:
        rv = report_visibility if report_visibility in _REPORT_VISIBILITY else "managers_only"
        pt = project_type if project_type in _PROJECT_TYPES else "time_and_materials"
        cur = (currency or "USD").strip().upper()[:10] or "USD"
        row = TimeManagerClientProjectModel(
            id=str(uuid.uuid4()),
            client_id=client_id,
            name=name.strip(),
            code=self._normalize_code(code),
            start_date=start_date,
            end_date=end_date,
            notes=notes,
            report_visibility=rv,
            project_type=pt,
            currency=cur,
            billable_rate_type=_strip_opt(billable_rate_type),
            project_billable_rate_amount=project_billable_rate_amount,
            budget_type=_strip_opt(budget_type),
            budget_amount=budget_amount,
            budget_hours=budget_hours,
            budget_resets_every_month=budget_resets_every_month,
            budget_includes_expenses=budget_includes_expenses,
            send_budget_alerts=send_budget_alerts,
            budget_alert_threshold_percent=budget_alert_threshold_percent,
            fixed_fee_amount=fixed_fee_amount,
            is_archived=bool(is_archived),
            created_at=_now_utc(),
            updated_at=None,
        )
        self._session.add(row)
        return row

    async def update(
        self,
        client_id: str,
        project_id: str,
        patch: dict[str, Any],
    ) -> TimeManagerClientProjectModel | None:
        row = await self.get_by_id(client_id, project_id)
        if not row:
            return None
        if "name" in patch and patch["name"] is not None:
            row.name = str(patch["name"]).strip()
        if "code" in patch:
            row.code = None if patch["code"] is None else self._normalize_code(str(patch["code"]))
        if "start_date" in patch:
            row.start_date = patch["start_date"]
        if "end_date" in patch:
            row.end_date = patch["end_date"]
        if "notes" in patch:
            row.notes = patch["notes"]
        if "report_visibility" in patch and patch["report_visibility"] is not None:
            rv = str(patch["report_visibility"])
            row.report_visibility = rv if rv in _REPORT_VISIBILITY else row.report_visibility
        if "project_type" in patch and patch["project_type"] is not None:
            pt = str(patch["project_type"])
            row.project_type = pt if pt in _PROJECT_TYPES else row.project_type
        if "billable_rate_type" in patch:
            row.billable_rate_type = _strip_opt(patch["billable_rate_type"])
        if "project_billable_rate_amount" in patch:
            row.project_billable_rate_amount = _decimal_none(patch["project_billable_rate_amount"])
        if "budget_type" in patch:
            row.budget_type = _strip_opt(patch["budget_type"])
        if "budget_amount" in patch:
            row.budget_amount = _decimal_none(patch["budget_amount"])
        if "budget_hours" in patch:
            row.budget_hours = _decimal_none(patch["budget_hours"])
        if "budget_resets_every_month" in patch:
            row.budget_resets_every_month = bool(patch["budget_resets_every_month"])
        if "budget_includes_expenses" in patch:
            row.budget_includes_expenses = bool(patch["budget_includes_expenses"])
        if "send_budget_alerts" in patch:
            row.send_budget_alerts = bool(patch["send_budget_alerts"])
        if "budget_alert_threshold_percent" in patch:
            row.budget_alert_threshold_percent = _decimal_none(patch["budget_alert_threshold_percent"])
        if "currency" in patch and patch["currency"] is not None:
            cur = str(patch["currency"]).strip().upper()[:10] or "USD"
            row.currency = cur
        if "fixed_fee_amount" in patch:
            row.fixed_fee_amount = _decimal_none(patch["fixed_fee_amount"])
        if "is_archived" in patch and patch["is_archived"] is not None:
            row.is_archived = bool(patch["is_archived"])
        row.updated_at = _now_utc()
        self._session.add(row)
        return row

    async def delete(self, client_id: str, project_id: str) -> bool:
        row = await self.get_by_id(client_id, project_id)
        if not row:
            return False
        await self._session.execute(
            delete(TimeManagerClientProjectModel).where(
                TimeManagerClientProjectModel.client_id == client_id,
                TimeManagerClientProjectModel.id == project_id,
            )
        )
        return True
