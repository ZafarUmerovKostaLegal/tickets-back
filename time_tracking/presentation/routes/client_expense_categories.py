

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from infrastructure.database import get_session
from infrastructure.repositories import ClientExpenseCategoryRepository
from presentation.routes.client_access import ensure_client_not_archived, get_client_or_404
from presentation.schemas import (
    TimeManagerClientExpenseCategoryCreateBody,
    TimeManagerClientExpenseCategoryOut,
    TimeManagerClientExpenseCategoryPatchBody,
)

router = APIRouter(prefix="/clients", tags=["client_expense_categories"])


def _category_out(row, usage: int) -> TimeManagerClientExpenseCategoryOut:
    return TimeManagerClientExpenseCategoryOut(
        id=row.id,
        client_id=row.client_id,
        name=row.name,
        has_unit_price=row.has_unit_price,
        is_archived=row.is_archived,
        sort_order=row.sort_order,
        created_at=row.created_at,
        updated_at=row.updated_at,
        usage_count=usage,
        deletable=usage == 0,
    )


async def _require_client(session: AsyncSession, client_id: str) -> None:
    await get_client_or_404(session, client_id)


async def _require_client_mutable(session: AsyncSession, client_id: str) -> None:
    row = await get_client_or_404(session, client_id)
    ensure_client_not_archived(row)


@router.get("/{client_id}/expense-categories", response_model=list[TimeManagerClientExpenseCategoryOut])
async def list_client_expense_categories(
    client_id: str,
    include_archived: bool = Query(False, alias="includeArchived"),
    session: AsyncSession = Depends(get_session),
):
    await _require_client(session, client_id)
    repo = ClientExpenseCategoryRepository(session)
    rows = await repo.list_for_client(client_id, include_archived=include_archived)
    out: list[TimeManagerClientExpenseCategoryOut] = []
    for r in rows:
        usage = await repo.usage_count(r.id)
        out.append(_category_out(r, usage))
    return out


@router.get(
    "/{client_id}/expense-categories/{category_id}",
    response_model=TimeManagerClientExpenseCategoryOut,
)
async def get_client_expense_category(
    client_id: str,
    category_id: str,
    session: AsyncSession = Depends(get_session),
):
    await _require_client(session, client_id)
    repo = ClientExpenseCategoryRepository(session)
    row = await repo.get_by_id(client_id, category_id)
    if not row:
        raise HTTPException(status_code=404, detail="Expense category not found")
    usage = await repo.usage_count(row.id)
    return _category_out(row, usage)


@router.post("/{client_id}/expense-categories", response_model=TimeManagerClientExpenseCategoryOut)
async def create_client_expense_category(
    client_id: str,
    body: TimeManagerClientExpenseCategoryCreateBody,
    session: AsyncSession = Depends(get_session),
):
    await _require_client_mutable(session, client_id)
    repo = ClientExpenseCategoryRepository(session)
    if await repo.has_active_name_conflict(client_id, body.name):
        raise HTTPException(
            status_code=409,
            detail="An active category with this name already exists for this client",
        )
    try:
        row = await repo.create(
            client_id=client_id,
            name=body.name,
            has_unit_price=body.has_unit_price,
            sort_order=body.sort_order,
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="An active category with this name already exists for this client",
        ) from None
    await session.refresh(row)
    usage = await repo.usage_count(row.id)
    return _category_out(row, usage)


@router.patch(
    "/{client_id}/expense-categories/{category_id}",
    response_model=TimeManagerClientExpenseCategoryOut,
)
async def patch_client_expense_category(
    client_id: str,
    category_id: str,
    body: TimeManagerClientExpenseCategoryPatchBody,
    session: AsyncSession = Depends(get_session),
):
    await _require_client_mutable(session, client_id)
    repo = ClientExpenseCategoryRepository(session)
    patch = body.model_dump(exclude_unset=True, mode="json", by_alias=False)
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")

    row = await repo.get_by_id(client_id, category_id)
    if not row:
        raise HTTPException(status_code=404, detail="Expense category not found")

    if "name" in patch and patch["name"] is not None:
        if await repo.has_active_name_conflict(
            client_id,
            str(patch["name"]),
            exclude_category_id=category_id,
        ):
            raise HTTPException(
                status_code=409,
                detail="An active category with this name already exists for this client",
            )

    if patch.get("is_archived") is False and row.is_archived:
        name_for_check = str(patch["name"]) if patch.get("name") is not None else row.name
        if await repo.has_active_name_conflict(
            client_id,
            name_for_check,
            exclude_category_id=category_id,
        ):
            raise HTTPException(
                status_code=409,
                detail="An active category with this name already exists for this client",
            )

    try:
        updated = await repo.update(client_id, category_id, patch)
        if not updated:
            raise HTTPException(status_code=404, detail="Expense category not found")
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="An active category with this name already exists for this client",
        ) from None

    await session.refresh(updated)
    usage = await repo.usage_count(updated.id)
    return _category_out(updated, usage)


@router.delete("/{client_id}/expense-categories/{category_id}", status_code=204)
async def delete_client_expense_category(
    client_id: str,
    category_id: str,
    session: AsyncSession = Depends(get_session),
):
    await _require_client_mutable(session, client_id)
    repo = ClientExpenseCategoryRepository(session)
    usage = await repo.usage_count(category_id)
    if usage > 0:
        raise HTTPException(
            status_code=409,
            detail="Category is in use and cannot be deleted; archive it instead",
        )
    ok = await repo.delete(client_id, category_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Expense category not found")
    await session.commit()
    return Response(status_code=204)
