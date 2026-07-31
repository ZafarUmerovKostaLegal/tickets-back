from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models_firm_bank import FirmBankProfileModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _s(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


class FirmBankProfileRepository:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def list_all(self) -> list[FirmBankProfileModel]:
        q = (
            select(FirmBankProfileModel)
            .order_by(
                FirmBankProfileModel.is_default.desc(),
                FirmBankProfileModel.sort_order.asc(),
                FirmBankProfileModel.created_at.asc(),
            )
        )
        return list((await self._s.execute(q)).scalars().all())

    async def get(self, profile_id: str) -> FirmBankProfileModel | None:
        return await self._s.get(FirmBankProfileModel, profile_id)

    async def clear_defaults(self) -> None:
        await self._s.execute(
            update(FirmBankProfileModel).values(is_default=False, updated_at=_now())
        )

    async def create(
        self,
        payload: dict[str, Any],
        *,
        actor_id: int,
        make_default: bool,
    ) -> FirmBankProfileModel:
        rows = await self.list_all()
        if make_default or len(rows) == 0:
            await self.clear_defaults()
            is_default = True
        else:
            is_default = False
        row = FirmBankProfileModel(
            id=_s(payload.get("id")) or str(uuid4()),
            title=_s(payload.get("title")),
            is_default=is_default,
            tin=_s(payload.get("tin")),
            bank_name=_s(payload.get("bankName") or payload.get("bank_name")),
            bank_address=_s(payload.get("bankAddress") or payload.get("bank_address")),
            account_currency=(_s(payload.get("accountCurrency") or payload.get("account_currency")) or "EUR").upper()[:16],
            account_number=_s(payload.get("accountNumber") or payload.get("account_number")),
            bank_code=_s(payload.get("bankCode") or payload.get("bank_code")),
            swift=_s(payload.get("swift")),
            correspondent_bank=_s(payload.get("correspondentBank") or payload.get("correspondent_bank")),
            correspondent_account=_s(payload.get("correspondentAccount") or payload.get("correspondent_account")),
            sort_order=int(payload.get("sortOrder") or payload.get("sort_order") or len(rows)),
            created_by_auth_user_id=actor_id,
            created_at=_now(),
            updated_at=_now(),
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def patch(
        self,
        row: FirmBankProfileModel,
        payload: dict[str, Any],
        *,
        make_default: bool | None = None,
    ) -> FirmBankProfileModel:
        mapping = {
            "title": "title",
            "tin": "tin",
            "bankName": "bank_name",
            "bank_name": "bank_name",
            "bankAddress": "bank_address",
            "bank_address": "bank_address",
            "accountCurrency": "account_currency",
            "account_currency": "account_currency",
            "accountNumber": "account_number",
            "account_number": "account_number",
            "bankCode": "bank_code",
            "bank_code": "bank_code",
            "swift": "swift",
            "correspondentBank": "correspondent_bank",
            "correspondent_bank": "correspondent_bank",
            "correspondentAccount": "correspondent_account",
            "correspondent_account": "correspondent_account",
            "sortOrder": "sort_order",
            "sort_order": "sort_order",
        }
        for src, attr in mapping.items():
            if src not in payload:
                continue
            val = payload.get(src)
            if attr == "account_currency":
                setattr(row, attr, (_s(val) or "EUR").upper()[:16])
            elif attr == "sort_order":
                try:
                    setattr(row, attr, int(val))
                except (TypeError, ValueError):
                    pass
            else:
                setattr(row, attr, _s(val))

        want_default = make_default
        if want_default is None and "isDefault" in payload:
            want_default = bool(payload.get("isDefault"))
        if want_default is None and "is_default" in payload:
            want_default = bool(payload.get("is_default"))
        if want_default is True:
            await self.clear_defaults()
            row.is_default = True
        elif want_default is False and row.is_default:
            # Keep at least one default unless deleting.
            pass

        row.updated_at = _now()
        await self._s.flush()
        return row

    async def set_default(self, profile_id: str) -> FirmBankProfileModel | None:
        row = await self.get(profile_id)
        if row is None:
            return None
        await self.clear_defaults()
        row.is_default = True
        row.updated_at = _now()
        await self._s.flush()
        return row

    async def delete(self, profile_id: str) -> bool:
        row = await self.get(profile_id)
        if row is None:
            return False
        was_default = row.is_default
        await self._s.delete(row)
        await self._s.flush()
        if was_default:
            remaining = await self.list_all()
            if remaining:
                remaining[0].is_default = True
                remaining[0].updated_at = _now()
                await self._s.flush()
        return True
