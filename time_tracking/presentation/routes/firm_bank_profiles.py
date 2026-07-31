from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from application.access_control import ensure_can_list_teams, ensure_can_manage_teams
from infrastructure.database import get_session
from infrastructure.repository_firm_bank import FirmBankProfileRepository
from presentation.deps import require_bearer_user

router = APIRouter(prefix="/firm-bank-profiles", tags=["firm_bank_profiles"])


class FirmBankProfileBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None
    title: str = ""
    is_default: bool = Field(False, alias="isDefault")
    tin: str = ""
    bank_name: str = Field("", alias="bankName")
    bank_address: str = Field("", alias="bankAddress")
    account_currency: str = Field("EUR", alias="accountCurrency")
    account_number: str = Field("", alias="accountNumber")
    bank_code: str = Field("", alias="bankCode")
    swift: str = ""
    correspondent_bank: str = Field("", alias="correspondentBank")
    correspondent_account: str = Field("", alias="correspondentAccount")
    sort_order: Optional[int] = Field(None, alias="sortOrder")


def _row_out(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title or "",
        "isDefault": bool(row.is_default),
        "tin": row.tin or "",
        "bankName": row.bank_name or "",
        "bankAddress": row.bank_address or "",
        "accountCurrency": (row.account_currency or "EUR").upper(),
        "accountNumber": row.account_number or "",
        "bankCode": row.bank_code or "",
        "swift": row.swift or "",
        "correspondentBank": row.correspondent_bank or "",
        "correspondentAccount": row.correspondent_account or "",
        "sortOrder": int(row.sort_order or 0),
        "createdByAuthUserId": int(row.created_by_auth_user_id or 0),
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("")
async def list_firm_bank_profiles(
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_bearer_user),
):
    ensure_can_list_teams(user)
    rows = await FirmBankProfileRepository(session).list_all()
    return {"items": [_row_out(r) for r in rows]}


@router.post("", status_code=201)
async def create_firm_bank_profile(
    body: FirmBankProfileBody,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_bearer_user),
):
    ensure_can_manage_teams(user)
    repo = FirmBankProfileRepository(session)
    row = await repo.create(
        body.model_dump(by_alias=True),
        actor_id=int(user.get("id") or 0),
        make_default=bool(body.is_default),
    )
    await session.commit()
    return _row_out(row)


@router.put("/replace")
async def replace_firm_bank_profiles(
    body: dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_bearer_user),
):
    """Bulk replace (used for one-time localStorage → server migration)."""
    ensure_can_manage_teams(user)
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="Ожидается { items: [...] }")
    repo = FirmBankProfileRepository(session)
    existing = await repo.list_all()
    for row in existing:
        await session.delete(row)
    await session.flush()
    actor = int(user.get("id") or 0)
    created = []
    for idx, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        payload = dict(raw)
        payload["sortOrder"] = payload.get("sortOrder", idx)
        row = await repo.create(
            payload,
            actor_id=actor,
            make_default=bool(payload.get("isDefault") or payload.get("is_default")),
        )
        created.append(row)
    # Ensure exactly one default when any rows exist.
    if created and not any(r.is_default for r in created):
        created[0].is_default = True
    await session.commit()
    rows = await repo.list_all()
    return {"items": [_row_out(r) for r in rows]}


@router.patch("/{profile_id}")
async def patch_firm_bank_profile(
    profile_id: str,
    body: dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_bearer_user),
):
    ensure_can_manage_teams(user)
    repo = FirmBankProfileRepository(session)
    row = await repo.get(profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Реквизиты не найдены")
    make_default = None
    if "isDefault" in body:
        make_default = bool(body.get("isDefault"))
    elif "is_default" in body:
        make_default = bool(body.get("is_default"))
    patched = await repo.patch(row, body, make_default=make_default)
    await session.commit()
    return _row_out(patched)


@router.post("/{profile_id}/set-default")
async def set_default_firm_bank_profile(
    profile_id: str,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_bearer_user),
):
    ensure_can_manage_teams(user)
    repo = FirmBankProfileRepository(session)
    row = await repo.set_default(profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Реквизиты не найдены")
    await session.commit()
    return _row_out(row)


@router.delete("/{profile_id}", status_code=204)
async def delete_firm_bank_profile(
    profile_id: str,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_bearer_user),
):
    ensure_can_manage_teams(user)
    repo = FirmBankProfileRepository(session)
    ok = await repo.delete(profile_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Реквизиты не найдены")
    await session.commit()
    return Response(status_code=204)
