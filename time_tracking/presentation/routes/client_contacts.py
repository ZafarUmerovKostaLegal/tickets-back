

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from infrastructure.database import get_session
from infrastructure.repositories import ClientContactRepository
from presentation.routes.client_access import ensure_client_not_archived, get_client_or_404
from presentation.schemas import (
    TimeManagerClientContactCreateBody,
    TimeManagerClientContactOut,
    TimeManagerClientContactPatchBody,
)

router = APIRouter(prefix="/clients", tags=["client_contacts"])


async def _require_client(session: AsyncSession, client_id: str) -> None:
    await get_client_or_404(session, client_id)


async def _require_client_mutable(session: AsyncSession, client_id: str) -> None:
    row = await get_client_or_404(session, client_id)
    ensure_client_not_archived(row)


@router.get("/{client_id}/contacts", response_model=list[TimeManagerClientContactOut])
async def list_client_contacts(client_id: str, session: AsyncSession = Depends(get_session)):
    await _require_client(session, client_id)
    repo = ClientContactRepository(session)
    rows = await repo.list_for_client(client_id)
    return [TimeManagerClientContactOut.model_validate(r) for r in rows]


@router.get(
    "/{client_id}/contacts/{contact_id}",
    response_model=TimeManagerClientContactOut,
)
async def get_client_contact(
    client_id: str,
    contact_id: str,
    session: AsyncSession = Depends(get_session),
):
    await _require_client(session, client_id)
    repo = ClientContactRepository(session)
    row = await repo.get_by_id(client_id, contact_id)
    if not row:
        raise HTTPException(status_code=404, detail="Contact not found")
    return TimeManagerClientContactOut.model_validate(row)


@router.post("/{client_id}/contacts", response_model=TimeManagerClientContactOut)
async def create_client_contact(
    client_id: str,
    body: TimeManagerClientContactCreateBody,
    session: AsyncSession = Depends(get_session),
):
    await _require_client_mutable(session, client_id)
    repo = ClientContactRepository(session)
    row = await repo.create(
        client_id=client_id,
        name=body.name,
        phone=body.phone,
        email=body.email,
        sort_order=body.sort_order,
    )
    await session.commit()
    return TimeManagerClientContactOut.model_validate(row)


@router.patch(
    "/{client_id}/contacts/{contact_id}",
    response_model=TimeManagerClientContactOut,
)
async def patch_client_contact(
    client_id: str,
    contact_id: str,
    body: TimeManagerClientContactPatchBody,
    session: AsyncSession = Depends(get_session),
):
    await _require_client_mutable(session, client_id)
    repo = ClientContactRepository(session)
    patch = body.model_dump(exclude_unset=True, mode="json", by_alias=False)
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")
    row = await repo.update(client_id, contact_id, patch)
    if not row:
        raise HTTPException(status_code=404, detail="Contact not found")
    await session.commit()
    return TimeManagerClientContactOut.model_validate(row)


@router.delete("/{client_id}/contacts/{contact_id}", status_code=204)
async def delete_client_contact(
    client_id: str,
    contact_id: str,
    session: AsyncSession = Depends(get_session),
):
    await _require_client_mutable(session, client_id)
    repo = ClientContactRepository(session)
    ok = await repo.delete(client_id, contact_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Contact not found")
    await session.commit()
    return Response(status_code=204)
