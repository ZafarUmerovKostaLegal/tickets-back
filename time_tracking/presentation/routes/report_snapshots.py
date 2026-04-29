

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from application.report_snapshot_overrides import (
    merge_frozen_and_overrides,
    validate_and_normalize_overrides,
)
from infrastructure.database import get_session
from infrastructure.models_reports import ReportSnapshotRowModel
from infrastructure.repository_reports import ReportSnapshotRepository
from presentation.deps import require_bearer_user

router = APIRouter(prefix="/reports", tags=["reports"])


def _json_obj(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        o = json.loads(raw)
        return o if isinstance(o, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


class SnapshotRowPatchBody(BaseModel):


    model_config = {"populate_by_name": True}

    overrides: dict[str, Any] = Field(
        ...,
        description="Только разрешённые поля; без id, projectCode, внешних ключей",
    )


def _snapshot_row_to_dict(r: ReportSnapshotRowModel) -> dict[str, Any]:
    data = _json_obj(r.frozen_data_json)
    ovr = _json_obj(r.overrides_json) if r.overrides_json else None
    effective = merge_frozen_and_overrides(data, ovr or {})
    return {
        "id": r.id,
        "sortOrder": r.sort_order,
        "sourceType": r.source_type,
        "sourceId": r.source_id,
        "data": data,
        "overrides": ovr,
        "effective": effective,
        "editedByUserId": r.edited_by_user_id,
        "editedAt": r.edited_at.isoformat() if r.edited_at else None,
    }


@router.get("/snapshots")
async def list_report_snapshots(
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> list[dict[str, Any]]:

    uid = int(viewer["id"])
    repo = ReportSnapshotRepository(session)
    rows = await repo.list_for_user(uid)
    out: list[dict[str, Any]] = []
    for m in rows:
        n = await repo.row_count(m.id)
        out.append(
            {
                "id": m.id,
                "name": m.name,
                "reportType": m.report_type,
                "groupBy": m.group_by,
                "version": m.version,
                "createdAt": m.created_at.isoformat(),
                "updatedAt": m.updated_at.isoformat() if m.updated_at else None,
                "rowCount": n,
            }
        )
    return out


@router.get("/snapshots/{snapshot_id}")
async def get_report_snapshot(
    snapshot_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> dict[str, Any]:

    uid = int(viewer["id"])
    repo = ReportSnapshotRepository(session)
    snap = await repo.get_by_id(snapshot_id, load_rows=True)
    if not snap or snap.created_by_user_id != uid:
        raise HTTPException(status_code=404, detail="Not Found")
    row_models: list[ReportSnapshotRowModel] = list(snap.rows or [])
    row_models.sort(key=lambda r: (r.sort_order, r.id))
    rows_out = [_snapshot_row_to_dict(r) for r in row_models]
    return {
        "id": snap.id,
        "name": snap.name,
        "reportType": snap.report_type,
        "groupBy": snap.group_by,
        "version": snap.version,
        "filters": _json_obj(snap.filters_json),
        "createdAt": snap.created_at.isoformat(),
        "updatedAt": snap.updated_at.isoformat() if snap.updated_at else None,
        "rows": rows_out,
    }


@router.patch("/snapshots/{snapshot_id}/rows/{row_id}")
async def patch_report_snapshot_row(
    snapshot_id: str,
    row_id: str,
    body: SnapshotRowPatchBody,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> dict[str, Any]:

    uid = int(viewer["id"])
    repo = ReportSnapshotRepository(session)
    snap = await repo.get_by_id(snapshot_id, load_rows=False)
    if not snap or snap.created_by_user_id != uid:
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        norm = validate_and_normalize_overrides(body.overrides)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = await repo.patch_row(
        snapshot_id, row_id, norm, edited_by_user_id=uid
    )
    if not row:
        raise HTTPException(status_code=404, detail="Not Found")
    await session.commit()
    await session.refresh(row)
    return _snapshot_row_to_dict(row)
