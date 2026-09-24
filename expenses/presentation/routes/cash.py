"""Company cash desk. Visible and writable only for partners."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from application.cash_ledger import (
    is_manual_cash_entry,
    manual_balance_delta,
    propagate_cash_delta,
    sync_cash_reimbursements,
)
from application.cash_money import cash_movement, format_money, parse_amount
from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.file_storage import save_cash_attachment
from infrastructure.models import CashAttachmentModel, CashBalanceModel, CashMovementModel, CashTrackedModel
from backend_common.media_path import safe_media_path
from presentation.deps import get_current_user

router = APIRouter(prefix="/expenses/cash", tags=["expenses-cash"])

_HISTORY_LIMIT = 40


class CashAmountBody(BaseModel):
    amount: str = Field(min_length=1, max_length=40)
    note: str = Field(default="", max_length=500)


class CashAttachmentOut(BaseModel):
    id: str
    fileName: str
    mimeType: str


class CashMovementOut(BaseModel):
    id: int
    kind: str
    amount: str
    note: str
    balanceBefore: str | None
    balanceAfter: str
    createdByUserId: int
    createdAt: str
    expenseId: str | None = None
    text: str
    attachments: list[CashAttachmentOut] = Field(default_factory=list)


class CashStateOut(BaseModel):
    balance: str | None
    balanceSet: bool
    history: list[CashMovementOut]


class CashChangeOut(BaseModel):
    balance: str
    message: str
    movement: CashMovementOut


class CashDeleteOut(BaseModel):
    balance: str


def _norm(value: str | None) -> str:
    return (value or "").strip().lower().replace("ё", "е")


def is_cash_partner(user: dict) -> bool:
    role = _norm(user.get("role"))
    position = _norm(user.get("position"))
    return any(token in role or token in position for token in ("партнер", "partner"))


def require_cash_partner(user: dict = Depends(get_current_user)) -> dict:
    if not is_cash_partner(user):
        raise HTTPException(status_code=403, detail="Касса доступна только партнёрам")
    return user


def _money_or_none(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format_money(value)


def _movement_text(row: CashMovementModel) -> str:
    note = row.note or ""
    after = Decimal(row.balance_after)
    amount = Decimal(row.amount)
    before = None if row.balance_before is None else Decimal(row.balance_before)
    if row.kind == "expense" and before is not None:
        return cash_movement(before, "Потрачено", amount, after, note)
    if row.kind == "topup" and before is not None:
        return cash_movement(before, "Пополнение", amount, after, note)
    if row.kind == "set":
        if before is not None:
            return (
                f"Остаток в кассе: {format_money(before)}\n"
                f"Остаток установлен: {format_money(after)}\n\n"
                f"Остаток на текущий момент: {format_money(after)}"
            )
        return (
            f"Остаток установлен: {format_money(after)}\n\n"
            f"Остаток на текущий момент: {format_money(after)}"
        )
    return format_money(after)


def _movement_out(row: CashMovementModel) -> CashMovementOut:
    return CashMovementOut(
        id=row.id,
        kind=row.kind,
        amount=format_money(Decimal(row.amount)),
        note=row.note or "",
        balanceBefore=_money_or_none(None if row.balance_before is None else Decimal(row.balance_before)),
        balanceAfter=format_money(Decimal(row.balance_after)),
        createdByUserId=row.created_by_user_id,
        createdAt=row.created_at.isoformat(),
        expenseId=row.expense_id,
        text=_movement_text(row),
        attachments=[
            CashAttachmentOut(id=item.id, fileName=item.file_name, mimeType=item.mime_type)
            for item in (row.__dict__.get("attachments") or [])
        ],
    )


_CASH_FILE_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".bmp",
    ".pdf",
    ".mp4", ".webm", ".mov", ".m4v",
    ".mp3", ".m4a", ".wav", ".ogg",
}
_MAX_CASH_FILES = 8


def _cash_file_ok(filename: str, content: bytes) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in _CASH_FILE_EXT:
        raise HTTPException(status_code=400, detail="Можно вложить скрин, скан PDF, фото, видео или аудио")
    if not content:
        raise HTTPException(status_code=400, detail="Файл пустой")
    if ext == ".pdf" and not content.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="Скан должен быть файлом PDF")
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        ok = (
            content.startswith(b"\x89PNG")
            or content.startswith(b"\xff\xd8\xff")
            or content.startswith(b"GIF8")
            or content.startswith(b"RIFF")
        )
        if not ok:
            raise HTTPException(status_code=400, detail="Файл изображения повреждён или это не картинка")
    return ext


async def _load_movement(session: AsyncSession, movement_id: int) -> CashMovementModel | None:
    return (
        await session.execute(
            select(CashMovementModel)
            .options(selectinload(CashMovementModel.attachments))
            .where(CashMovementModel.id == movement_id)
        )
    ).scalar_one_or_none()


async def _lock_balance(session: AsyncSession) -> CashBalanceModel:
    row = (
        await session.execute(
            select(CashBalanceModel).where(CashBalanceModel.id == 1).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        row = CashBalanceModel(id=1, balance=None, balance_set=False, updated_at=None)
        session.add(row)
        await session.flush()
    return row


def _parse_body_amount(raw: str) -> Decimal:
    amount = parse_amount(raw)
    if amount is None:
        raise HTTPException(status_code=422, detail="Отправьте сумму числом, например 298000")
    return amount


def _search_like(raw: str) -> str:
    escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def _history(session: AsyncSession, q: str | None = None) -> list[CashMovementOut]:
    stmt = (
        select(CashMovementModel)
        .options(selectinload(CashMovementModel.attachments))
        .order_by(
        CashMovementModel.created_at.desc(),
        CashMovementModel.id.desc(),
        )
    )
    term = (q or "").strip()
    if term:
        like = _search_like(term)
        stmt = stmt.where(
            or_(
                CashMovementModel.note.ilike(like, escape="\\"),
                CashMovementModel.expense_id.ilike(like, escape="\\"),
            )
        ).limit(500)
    else:
        stmt = stmt.limit(_HISTORY_LIMIT)
    rows = (await session.execute(stmt)).scalars().all()
    return [_movement_out(row) for row in rows]


@router.get("", response_model=CashStateOut)
async def get_cash(
    q: str | None = Query(None, max_length=200, description="Поиск по заметке и номеру заявки по всей истории"),
    user: dict = Depends(require_cash_partner),
    session: AsyncSession = Depends(get_session),
) -> CashStateOut:
    await sync_cash_reimbursements(session, int(user["id"]))
    await session.commit()
    history = await _history(session, q)
    row = (
        await session.execute(select(CashBalanceModel).where(CashBalanceModel.id == 1))
    ).scalar_one_or_none()
    if row is None or not row.balance_set or row.balance is None:
        return CashStateOut(balance=None, balanceSet=False, history=history)
    return CashStateOut(
        balance=format_money(Decimal(row.balance)),
        balanceSet=True,
        history=history,
    )


@router.post("/balance", response_model=CashChangeOut)
async def set_cash_balance(
    body: CashAmountBody,
    user: dict = Depends(require_cash_partner),
    session: AsyncSession = Depends(get_session),
) -> CashChangeOut:
    amount = _parse_body_amount(body.amount)
    row = await _lock_balance(session)
    before = None if not row.balance_set or row.balance is None else Decimal(row.balance)
    now = datetime.now(timezone.utc)
    row.balance = amount
    row.balance_set = True
    row.baseline_done = False
    row.balance_set_at = now
    row.updated_at = now
    await session.execute(delete(CashTrackedModel))
    movement = CashMovementModel(
        kind="set",
        amount=amount,
        note=" ".join((body.note or "").split()),
        balance_before=before,
        balance_after=amount,
        created_by_user_id=int(user["id"]),
        created_at=now,
    )
    session.add(movement)
    await session.flush()
    await sync_cash_reimbursements(session, int(user["id"]))
    await session.commit()
    await session.refresh(movement)
    return CashChangeOut(
        balance=format_money(Decimal(row.balance if row.balance is not None else amount)),
        message=_movement_text(movement),
        movement=_movement_out(movement),
    )


async def _move(
    session: AsyncSession,
    user: dict,
    body: CashAmountBody,
    *,
    expense: bool,
) -> CashChangeOut:
    amount = _parse_body_amount(body.amount)
    if amount <= 0:
        raise HTTPException(status_code=422, detail="Отправьте сумму больше нуля, например 50000")
    row = await _lock_balance(session)
    if not row.balance_set or row.balance is None:
        raise HTTPException(status_code=409, detail="Сначала задайте остаток")
    before = Decimal(row.balance)
    after = before - amount if expense else before + amount
    note = " ".join((body.note or "").split())
    now = datetime.now(timezone.utc)
    row.balance = after
    row.updated_at = now
    kind = "expense" if expense else "topup"
    movement = CashMovementModel(
        kind=kind,
        amount=amount,
        note=note,
        balance_before=before,
        balance_after=after,
        created_by_user_id=int(user["id"]),
        created_at=now,
    )
    session.add(movement)
    await session.commit()
    await session.refresh(movement)
    label = "Потрачено" if expense else "Пополнение"
    return CashChangeOut(
        balance=format_money(after),
        message=cash_movement(before, label, amount, after, note),
        movement=_movement_out(movement),
    )


@router.post("/expense", response_model=CashChangeOut)
async def record_cash_expense(
    body: CashAmountBody,
    user: dict = Depends(require_cash_partner),
    session: AsyncSession = Depends(get_session),
) -> CashChangeOut:
    return await _move(session, user, body, expense=True)


@router.post("/topup", response_model=CashChangeOut)
async def record_cash_topup(
    body: CashAmountBody,
    user: dict = Depends(require_cash_partner),
    session: AsyncSession = Depends(get_session),
) -> CashChangeOut:
    return await _move(session, user, body, expense=False)


async def _manual_movement(session: AsyncSession, movement_id: int) -> CashMovementModel:
    row = await session.get(CashMovementModel, movement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if not is_manual_cash_entry(row.kind, row.expense_id):
        raise HTTPException(
            status_code=409,
            detail="Эту запись добавила заявка на расход. Её можно изменить только на странице расходов",
        )
    return row


async def _movements_after(session: AsyncSession, row: CashMovementModel) -> list[CashMovementModel]:
    rows = (
        await session.execute(
            select(CashMovementModel)
            .where(
                or_(
                    CashMovementModel.created_at > row.created_at,
                    and_(
                        CashMovementModel.created_at == row.created_at,
                        CashMovementModel.id > row.id,
                    ),
                )
            )
            .order_by(CashMovementModel.created_at.asc(), CashMovementModel.id.asc())
        )
    ).scalars().all()
    return list(rows)


def _apply_delta(balance: CashBalanceModel, later: list[CashMovementModel], delta: Decimal) -> None:
    if not propagate_cash_delta(later, delta):
        return
    if balance.balance is None:
        return
    balance.balance = Decimal(balance.balance) + delta
    balance.updated_at = datetime.now(timezone.utc)


@router.patch("/movements/{movement_id}", response_model=CashChangeOut)
async def update_cash_movement(
    movement_id: int,
    body: CashAmountBody,
    user: dict = Depends(require_cash_partner),
    session: AsyncSession = Depends(get_session),
) -> CashChangeOut:
    amount = _parse_body_amount(body.amount)
    if amount <= 0:
        raise HTTPException(status_code=422, detail="Отправьте сумму больше нуля, например 50000")
    balance = await _lock_balance(session)
    row = await _manual_movement(session, movement_id)
    old_amount = Decimal(row.amount)
    delta = manual_balance_delta(kind=row.kind, old_amount=old_amount, new_amount=amount)
    note = " ".join((body.note or "").split())
    row.amount = amount
    row.note = note
    if row.balance_before is not None:
        sign = Decimal("-1") if row.kind == "expense" else Decimal("1")
        row.balance_after = Decimal(row.balance_before) + sign * amount
    else:
        row.balance_after = Decimal(row.balance_after) + delta
    later = await _movements_after(session, row)
    _apply_delta(balance, later, delta)
    await session.commit()
    await session.refresh(row)
    label = "Потрачено" if row.kind == "expense" else "Пополнение"
    before = Decimal("0") if row.balance_before is None else Decimal(row.balance_before)
    after = Decimal(row.balance_after)
    return CashChangeOut(
        balance=format_money(Decimal(balance.balance if balance.balance is not None else after)),
        message=cash_movement(before, label, amount, after, note),
        movement=_movement_out(row),
    )


@router.delete("/movements/{movement_id}", response_model=CashDeleteOut)
async def delete_cash_movement(
    movement_id: int,
    user: dict = Depends(require_cash_partner),
    session: AsyncSession = Depends(get_session),
) -> CashDeleteOut:
    balance = await _lock_balance(session)
    row = await _manual_movement(session, movement_id)
    delta = manual_balance_delta(kind=row.kind, old_amount=Decimal(row.amount), new_amount=None)
    later = await _movements_after(session, row)
    stored = (
        await session.execute(
            select(CashAttachmentModel).where(CashAttachmentModel.movement_id == row.id)
        )
    ).scalars().all()
    for item in stored:
        path = safe_media_path(get_settings().media_path, item.storage_key)
        if path is not None and path.is_file():
            path.unlink()
    await session.delete(row)
    _apply_delta(balance, later, delta)
    await session.commit()
    shown = Decimal("0") if balance.balance is None else Decimal(balance.balance)
    return CashDeleteOut(balance=format_money(shown))


_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".bmp": "image/bmp",
    ".pdf": "application/pdf",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".m4v": "video/mp4",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
}


@router.post("/movements/{movement_id}/attachments", response_model=CashMovementOut)
async def upload_cash_attachment(
    movement_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(require_cash_partner),
    session: AsyncSession = Depends(get_session),
) -> CashMovementOut:
    row = await _load_movement(session, movement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if len(row.attachments or []) >= _MAX_CASH_FILES:
        raise HTTPException(status_code=400, detail=f"К записи можно прикрепить не больше {_MAX_CASH_FILES} файлов")
    content = await file.read()
    ext = _cash_file_ok(file.filename or "", content)
    try:
        storage_key, safe_name = save_cash_attachment(movement_id, file.filename or f"file{ext}", content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.add(CashAttachmentModel(
        id=str(uuid.uuid4()),
        movement_id=movement_id,
        file_name=safe_name[:255],
        mime_type=_MIME_BY_EXT.get(ext, "application/octet-stream"),
        storage_key=storage_key,
        created_at=datetime.now(timezone.utc),
    ))
    await session.commit()
    loaded = await _load_movement(session, movement_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return _movement_out(loaded)


@router.get("/movements/{movement_id}/attachments/{attachment_id}/file")
async def download_cash_attachment(
    movement_id: int,
    attachment_id: str,
    user: dict = Depends(require_cash_partner),
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    row = (
        await session.execute(
            select(CashAttachmentModel).where(
                CashAttachmentModel.id == attachment_id,
                CashAttachmentModel.movement_id == movement_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    path = safe_media_path(get_settings().media_path, row.storage_key)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(path, media_type=row.mime_type, filename=row.file_name, content_disposition_type="inline")


@router.delete("/movements/{movement_id}/attachments/{attachment_id}", response_model=CashMovementOut)
async def delete_cash_attachment(
    movement_id: int,
    attachment_id: str,
    user: dict = Depends(require_cash_partner),
    session: AsyncSession = Depends(get_session),
) -> CashMovementOut:
    row = (
        await session.execute(
            select(CashAttachmentModel).where(
                CashAttachmentModel.id == attachment_id,
                CashAttachmentModel.movement_id == movement_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    path = safe_media_path(get_settings().media_path, row.storage_key)
    if path is not None and path.is_file():
        path.unlink()
    await session.delete(row)
    await session.commit()
    loaded = await _load_movement(session, movement_id)
    if loaded is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return _movement_out(loaded)
