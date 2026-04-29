

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from infrastructure.models_reports import (
    ReportSavedViewModel,
    ReportSnapshotModel,
    ReportSnapshotRowModel,
)
from infrastructure.repository_shared import _now_utc


class ReportSavedViewRepository:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def list_for_user(self, owner_user_id: int) -> list[ReportSavedViewModel]:
        q = (
            select(ReportSavedViewModel)
            .where(ReportSavedViewModel.owner_user_id == owner_user_id)
            .order_by(ReportSavedViewModel.created_at.desc())
        )
        return list((await self._s.execute(q)).scalars().all())

    async def get_by_id(self, view_id: str) -> ReportSavedViewModel | None:
        return (
            await self._s.execute(
                select(ReportSavedViewModel).where(ReportSavedViewModel.id == view_id)
            )
        ).scalars().one_or_none()

    async def create(
        self,
        *,
        name: str,
        owner_user_id: int,
        filters: dict,
    ) -> ReportSavedViewModel:
        row = ReportSavedViewModel(
            id=str(uuid.uuid4()),
            name=name.strip(),
            owner_user_id=owner_user_id,
            filters_json=json.dumps(filters, ensure_ascii=False, default=str),
            created_at=_now_utc(),
            updated_at=None,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def update(
        self, view_id: str, patch: dict[str, Any]
    ) -> ReportSavedViewModel | None:
        row = await self.get_by_id(view_id)
        if not row:
            return None
        if "name" in patch and patch["name"] is not None:
            row.name = str(patch["name"]).strip()
        if "filters" in patch and patch["filters"] is not None:
            row.filters_json = json.dumps(
                patch["filters"], ensure_ascii=False, default=str
            )
        row.updated_at = _now_utc()
        self._s.add(row)
        return row

    async def delete(self, view_id: str) -> bool:
        row = await self.get_by_id(view_id)
        if not row:
            return False
        await self._s.execute(
            delete(ReportSavedViewModel).where(ReportSavedViewModel.id == view_id)
        )
        return True


class ReportSnapshotRepository:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def list_for_user(self, user_id: int) -> list[ReportSnapshotModel]:
        q = (
            select(ReportSnapshotModel)
            .where(ReportSnapshotModel.created_by_user_id == user_id)
            .order_by(ReportSnapshotModel.created_at.desc())
        )
        return list((await self._s.execute(q)).scalars().all())

    async def get_by_id(
        self, snapshot_id: str, *, load_rows: bool = False
    ) -> ReportSnapshotModel | None:
        q = select(ReportSnapshotModel).where(ReportSnapshotModel.id == snapshot_id)
        if load_rows:
            q = q.options(selectinload(ReportSnapshotModel.rows))
        return (await self._s.execute(q)).scalars().one_or_none()

    async def create(
        self,
        *,
        name: str,
        report_type: str,
        group_by: str | None,
        filters: dict,
        created_by_user_id: int,
        rows_data: list[dict],
    ) -> ReportSnapshotModel:
        snap_id = str(uuid.uuid4())
        now = _now_utc()
        snap = ReportSnapshotModel(
            id=snap_id,
            name=name.strip(),
            report_type=report_type,
            group_by=group_by,
            filters_json=json.dumps(filters, ensure_ascii=False, default=str),
            version=1,
            created_by_user_id=created_by_user_id,
            created_at=now,
            updated_at=None,
        )
        self._s.add(snap)
        for idx, rd in enumerate(rows_data):
            row = ReportSnapshotRowModel(
                id=str(uuid.uuid4()),
                snapshot_id=snap_id,
                sort_order=idx,
                source_type=rd.get("source_type", "unknown"),
                source_id=str(rd.get("source_id", "")),
                frozen_data_json=json.dumps(
                    rd.get("data", {}), ensure_ascii=False, default=str
                ),
                overrides_json=None,
                edited_by_user_id=None,
                edited_at=None,
            )
            self._s.add(row)
        await self._s.flush()
        return snap

    async def get_row(
        self, snapshot_id: str, row_id: str
    ) -> ReportSnapshotRowModel | None:
        q = select(ReportSnapshotRowModel).where(
            ReportSnapshotRowModel.snapshot_id == snapshot_id,
            ReportSnapshotRowModel.id == row_id,
        )
        return (await self._s.execute(q)).scalars().one_or_none()

    async def patch_row(
        self,
        snapshot_id: str,
        row_id: str,
        overrides: dict,
        edited_by_user_id: int,
    ) -> ReportSnapshotRowModel | None:
        row = await self.get_row(snapshot_id, row_id)
        if not row:
            return None
        existing: dict = {}
        if row.overrides_json:
            try:
                existing = json.loads(row.overrides_json)
            except (json.JSONDecodeError, TypeError):
                pass
        existing.update(overrides)
        row.overrides_json = json.dumps(existing, ensure_ascii=False, default=str)
        row.edited_by_user_id = edited_by_user_id
        row.edited_at = _now_utc()
        self._s.add(row)
        snap = await self.get_by_id(snapshot_id)
        if snap:
            snap.updated_at = _now_utc()
            self._s.add(snap)
        return row

    async def rebuild_rows(
        self,
        snapshot_id: str,
        rows_data: list[dict],
    ) -> ReportSnapshotModel | None:
        snap = await self.get_by_id(snapshot_id)
        if not snap:
            return None
        await self._s.execute(
            delete(ReportSnapshotRowModel).where(
                ReportSnapshotRowModel.snapshot_id == snapshot_id
            )
        )
        for idx, rd in enumerate(rows_data):
            row = ReportSnapshotRowModel(
                id=str(uuid.uuid4()),
                snapshot_id=snapshot_id,
                sort_order=idx,
                source_type=rd.get("source_type", "unknown"),
                source_id=str(rd.get("source_id", "")),
                frozen_data_json=json.dumps(
                    rd.get("data", {}), ensure_ascii=False, default=str
                ),
                overrides_json=None,
                edited_by_user_id=None,
                edited_at=None,
            )
            self._s.add(row)
        snap.version += 1
        snap.updated_at = _now_utc()
        self._s.add(snap)
        await self._s.flush()
        return snap

    async def delete(self, snapshot_id: str) -> bool:
        snap = await self.get_by_id(snapshot_id)
        if not snap:
            return False
        await self._s.execute(
            delete(ReportSnapshotRowModel).where(
                ReportSnapshotRowModel.snapshot_id == snapshot_id
            )
        )
        await self._s.execute(
            delete(ReportSnapshotModel).where(ReportSnapshotModel.id == snapshot_id)
        )
        return True

    async def row_count(self, snapshot_id: str) -> int:
        q = (
            select(func.count())
            .select_from(ReportSnapshotRowModel)
            .where(ReportSnapshotRowModel.snapshot_id == snapshot_id)
        )
        return int((await self._s.execute(q)).scalar_one() or 0)
