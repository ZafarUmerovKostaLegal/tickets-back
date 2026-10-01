from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import get_session
from infrastructure.models import AccountingSettingModel
from presentation.deps import require_hr_user

router = APIRouter(prefix="/accounting-settings", tags=["accounting-settings"])

_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


class AccountingSettingBody(BaseModel):
    value: str = Field(default="", max_length=4000)


def _payload(row: AccountingSettingModel) -> dict:
    updated = row.updated_at.isoformat() if row.updated_at else None
    return {"key": row.key, "value": row.value or "", "updatedAt": updated}


def _check_key(key: str) -> str:
    cleaned = (key or "").strip()
    if not _KEY_RE.fullmatch(cleaned):
        raise HTTPException(status_code=400, detail="Ключ настройки: латиница, цифры, точка, дефис или подчёркивание")
    return cleaned


@router.get("")
async def list_accounting_settings(
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_hr_user),
):
    rows = list((await session.execute(
        select(AccountingSettingModel).order_by(AccountingSettingModel.key.asc())
    )).scalars().all())
    return {"items": [_payload(row) for row in rows]}


@router.put("/{key}")
async def upsert_accounting_setting(
    key: str,
    body: AccountingSettingBody,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_hr_user),
):
    cleaned = _check_key(key)
    row = await session.get(AccountingSettingModel, cleaned)
    now = datetime.now(timezone.utc)
    if row is None:
        row = AccountingSettingModel(key=cleaned, value=body.value, updated_by=int(user["id"]), updated_at=now)
        session.add(row)
    else:
        row.value = body.value
        row.updated_by = int(user["id"])
        row.updated_at = now
    await session.commit()
    await session.refresh(row)
    return _payload(row)


@router.delete("/{key}", status_code=204)
async def delete_accounting_setting(
    key: str,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_hr_user),
):
    cleaned = _check_key(key)
    row = await session.get(AccountingSettingModel, cleaned)
    if row is None:
        raise HTTPException(status_code=404, detail="Настройка не найдена")
    await session.delete(row)
    await session.commit()
    return None
