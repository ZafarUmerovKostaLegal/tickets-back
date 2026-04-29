

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from application.client_expense_category_defaults import seed_default_expense_categories_for_client
from application.client_task_defaults import seed_default_common_tasks_for_client
from infrastructure.database import get_session
from infrastructure.repositories import ClientRepository
from presentation.schemas import (
    TimeManagerClientContactOut,
    TimeManagerClientCreateBody,
    TimeManagerClientOut,
    TimeManagerClientPatchBody,
)

router = APIRouter(prefix="/clients", tags=["clients"])


def _to_out(row, extra_contacts: list | None = None) -> TimeManagerClientOut:
    ec = (
        [TimeManagerClientContactOut.model_validate(c) for c in extra_contacts]
        if extra_contacts is not None
        else []
    )
    return TimeManagerClientOut(
        id=row.id,
        name=row.name,
        address=row.address,
        currency=row.currency,
        invoice_due_mode=row.invoice_due_mode,
        invoice_due_days_after_issue=row.invoice_due_days_after_issue,
        tax_percent=row.tax_percent,
        tax2_percent=row.tax2_percent,
        discount_percent=row.discount_percent,
        phone=row.phone,
        email=row.email,
        contact_name=row.contact_name,
        contact_phone=row.contact_phone,
        contact_email=row.contact_email,
        extra_contacts=ec,
        is_archived=row.is_archived,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("")
async def list_clients(
    include_archived: bool = Query(False, alias="includeArchived"),
    limit: int | None = Query(None, ge=1, le=500, description="Если задано — пагинированный ответ"),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    repo = ClientRepository(session)
    if limit is None:
        rows = await repo.list_all(include_archived=include_archived)
        return [_to_out(r) for r in rows]
    rows, total = await repo.list_all_paginated(
        include_archived=include_archived, limit=limit, offset=offset
    )
    return {
        "items": [_to_out(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{client_id}", response_model=TimeManagerClientOut)
async def get_client(client_id: str, session: AsyncSession = Depends(get_session)):
    repo = ClientRepository(session)
    row = await repo.get_by_id_with_contacts(client_id)
    if not row:
        raise HTTPException(status_code=404, detail="Client not found")
    return _to_out(row, list(row.extra_contacts or []))


@router.post("", response_model=TimeManagerClientOut)
async def create_client(
    body: TimeManagerClientCreateBody,
    session: AsyncSession = Depends(get_session),
):
    repo = ClientRepository(session)
    row = await repo.create(
        name=body.name,
        address=body.address,
        currency=body.currency,
        invoice_due_mode=body.invoice_due_mode,
        invoice_due_days_after_issue=body.invoice_due_days_after_issue,
        tax_percent=body.tax_percent,
        tax2_percent=body.tax2_percent,
        discount_percent=body.discount_percent,
        phone=body.phone,
        email=body.email,
        contact_name=body.contact_name,
        contact_phone=body.contact_phone,
        contact_email=body.contact_email,
        is_archived=body.is_archived,
    )
    await seed_default_common_tasks_for_client(session, row.id)
    await seed_default_expense_categories_for_client(session, row.id)
    await session.commit()
    row = await repo.get_by_id_with_contacts(row.id)
    assert row is not None
    return _to_out(row, list(row.extra_contacts or []))


@router.patch("/{client_id}", response_model=TimeManagerClientOut)
async def patch_client(
    client_id: str,
    body: TimeManagerClientPatchBody,
    session: AsyncSession = Depends(get_session),
):
    repo = ClientRepository(session)
    patch = body.model_dump(exclude_unset=True, mode="json", by_alias=False)
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "is_archived" in patch and patch["is_archived"] is not None:
        patch["is_archived"] = bool(patch["is_archived"])
    row = await repo.update(client_id, patch)
    if not row:
        raise HTTPException(status_code=404, detail="Client not found")
    await session.commit()
    row = await repo.get_by_id_with_contacts(client_id)
    assert row is not None
    return _to_out(row, list(row.extra_contacts or []))


@router.delete("/{client_id}", status_code=204)
async def delete_client(client_id: str, session: AsyncSession = Depends(get_session)):
    repo = ClientRepository(session)
    ok = await repo.delete(client_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Client not found")
    await session.commit()
    return Response(status_code=204)
