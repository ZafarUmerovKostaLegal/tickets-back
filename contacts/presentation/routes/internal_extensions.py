from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import get_session
from infrastructure.models import InternalExtensionModel
from infrastructure.repositories import DuplicateExtensionError, InternalExtensionRepository
from presentation.dependencies import require_colleagues_access, require_internal_extensions_manage
from presentation.schemas import InternalExtensionCreateBody, InternalExtensionOut, InternalExtensionPatchBody

router = APIRouter(prefix="/internal-extensions", tags=["internal-extensions"])


def _clean_name(value: str) -> str:
    name = " ".join((value or "").split())
    return name[:200]


def _clean_extension(value: str) -> str:
    ext = (value or "").strip()
    return ext[:32]


def _to_out(row: InternalExtensionModel) -> InternalExtensionOut:
    return InternalExtensionOut(id=row.id, full_name=row.full_name, extension=row.extension)


@router.get("", response_model=list[InternalExtensionOut])
async def list_internal_extensions(
    _user: dict = Depends(require_colleagues_access),
    session: AsyncSession = Depends(get_session),
):
    rows = await InternalExtensionRepository(session).list_all()
    return [_to_out(row) for row in rows]


@router.post("", response_model=InternalExtensionOut, status_code=201)
async def create_internal_extension(
    body: InternalExtensionCreateBody,
    _user: dict = Depends(require_internal_extensions_manage),
    session: AsyncSession = Depends(get_session),
):
    name = _clean_name(body.full_name)
    extension = _clean_extension(body.extension)
    if not name or not extension:
        raise HTTPException(status_code=400, detail="Укажите имя и внутренний номер")
    repo = InternalExtensionRepository(session)
    try:
        row = await repo.create(full_name=name, extension=extension)
        await session.commit()
        await session.refresh(row)
    except DuplicateExtensionError:
        raise HTTPException(status_code=409, detail="Такой внутренний номер уже есть") from None
    return _to_out(row)


@router.patch("/{extension_id}", response_model=InternalExtensionOut)
async def patch_internal_extension(
    extension_id: int,
    body: InternalExtensionPatchBody,
    _user: dict = Depends(require_internal_extensions_manage),
    session: AsyncSession = Depends(get_session),
):
    repo = InternalExtensionRepository(session)
    row = await repo.get(extension_id)
    if not row:
        raise HTTPException(status_code=404, detail="Контакт не найден")
    name = _clean_name(body.full_name) if body.full_name is not None else None
    extension = _clean_extension(body.extension) if body.extension is not None else None
    if name is not None and not name:
        raise HTTPException(status_code=400, detail="Имя не может быть пустым")
    if extension is not None and not extension:
        raise HTTPException(status_code=400, detail="Номер не может быть пустым")
    try:
        row = await repo.update(row, full_name=name, extension=extension)
        await session.commit()
        await session.refresh(row)
    except DuplicateExtensionError:
        raise HTTPException(status_code=409, detail="Такой внутренний номер уже есть") from None
    return _to_out(row)


@router.delete("/{extension_id}", status_code=204)
async def delete_internal_extension(
    extension_id: int,
    _user: dict = Depends(require_internal_extensions_manage),
    session: AsyncSession = Depends(get_session),
):
    repo = InternalExtensionRepository(session)
    row = await repo.get(extension_id)
    if not row:
        raise HTTPException(status_code=404, detail="Контакт не найден")
    await repo.delete(row)
    await session.commit()
