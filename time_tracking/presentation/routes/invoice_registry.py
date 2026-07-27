from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import get_session
from infrastructure.repository_invoice_registry import InvoiceRegistryRepository
from presentation.deps import require_bearer_user

router = APIRouter(prefix="/invoice-registry", tags=["invoice_registry"])


ARCHIVE_YEARS: tuple[str, ...] = ("2025", "2024", "2023", "2022", "2021", "2020", "checklist")
SHEET_NAMES: dict[str, str] = {
    "2026": "Инвойс 2026",
    "2025": "Инвойс 2025",
    "2024": "Инвойс 2024",
    "2023": "Инвойс 2023",
    "2022": "Инвойс 2022",
    "2021": "Инвойс 2021",
    "2020": "Инвойс 2020",
    "checklist": "check list",
}
COL_2026: list[dict[str, str]] = [
    {"key": "seqNo", "label": "№"},
    {"key": "billedTo", "label": "Кому выставлено"},
    {"key": "currency", "label": "Валюта"},
    {"key": "amount", "label": "Выставленная сумма"},
    {"key": "details", "label": "Детали (краткое описание за какую работу этот инвойс)"},
    {"key": "partner", "label": "Партнёр"},
    {"key": "issueDate", "label": "Дата выставления"},
    {"key": "dueOrPayment", "label": "Предполагаемая дата оплаты"},
    {"key": "clientNumber", "label": "Номер инвойса для Клиента"},
    {"key": "statusNote", "label": "Статус", "editor": "status"},
    {"key": "advanceFee", "label": "Пред.вознаграждение"},
    {"key": "balance", "label": "Остаток"},
]
CANON_STATUSES: set[str] = {
    "Черновик",
    "На согласовании с Клиентом",
    "Выставлен",
    "Оплачен",
}
_CURRENCY_MAP: dict[str, str] = {"USZ": "UZS", "UZD": "UZS", "GBH": "GBP"}
_PARTNER_MAP: dict[str, str] = {
    "AA": "AAA",
    "VG": "VGB",
    "VBG": "VGB",
    "NH": "NFH",
    "NF": "NFH",
    "MD": "MAD",
    "SHYU": "SHMYU",
    "SHMYU": "SHMYU",
    "SHY": "SHMYU",
}


def _parse_amount(raw: str) -> float | None:
    s = (raw or "").strip().replace("\u00a0", "").replace(" ", "")
    if not s or re.fullmatch(r"[-—–]+", s):
        return None
    last_comma = s.rfind(",")
    last_dot = s.rfind(".")
    if last_comma >= 0 and last_dot >= 0:
        if last_comma > last_dot:
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif last_comma >= 0:
        frac = len(s) - last_comma - 1
        s = s.replace(",", ".") if frac in (1, 2) else s.replace(",", "")
    elif last_dot >= 0:
        if s.count(".") > 1:
            s = s.replace(".", "")
        elif s.endswith("."):
            s = s[:-1]
    try:
        n = float(s)
    except ValueError:
        return None
    return n if n > 0 else None


def _normalize_currency(raw: str) -> str:
    t = (raw or "").strip().upper()
    if not t:
        return ""
    return _CURRENCY_MAP.get(t, t)


def _normalize_partner(raw: str) -> str:
    t = (raw or "").strip()
    if not t:
        return ""
    return _PARTNER_MAP.get(t, _PARTNER_MAP.get(t.upper(), t))


def _row_to_payload(row: Any) -> dict[str, str]:
    return {
        "id": str(row.id),
        "seqNo": row.seq_no or "",
        "billedTo": row.billed_to or "",
        "currency": row.currency or "",
        "amount": row.amount or "",
        "details": row.details or "",
        "partner": row.partner or "",
        "issueDate": row.issue_date or "",
        "dueOrPayment": row.due_or_payment or "",
        "clientNumber": row.client_number or "",
        "statusNote": row.status_note or "",
        "advanceFee": row.advance_fee or "",
        "balance": row.balance or "",
    }


class RegistryRowBody(BaseModel):
    id: str | None = None
    seqNo: str = ""
    billedTo: str = ""
    currency: str = ""
    amount: str = ""
    details: str = ""
    partner: str = ""
    issueDate: str = ""
    dueOrPayment: str = ""
    clientNumber: str = ""
    statusNote: str = ""
    advanceFee: str = ""
    balance: str = ""


class RegistryRowsReplaceBody(BaseModel):
    rows: list[RegistryRowBody] = Field(default_factory=list)


@router.get("/years")
async def list_invoice_registry_years(
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_bearer_user),
):
    repo = InvoiceRegistryRepository(session)
    active_count = await repo.count_2026_rows()
    archive_rows = await repo.list_archive_sheets()
    archive_count_by_id = {r.year_id: len(_safe_rows_json(r.rows_json)) for r in archive_rows}
    years = [
        {"id": "2026", "sheetName": SHEET_NAMES["2026"], "mode": "active", "rowCount": active_count},
        *[
            {
                "id": y,
                "sheetName": SHEET_NAMES[y],
                "mode": "archive",
                "rowCount": int(archive_count_by_id.get(y, 0)),
            }
            for y in ARCHIVE_YEARS
        ],
    ]
    return {"years": years}


def _safe_rows_json(raw: str) -> list[dict[str, Any]]:
    import json

    try:
        parsed = json.loads(raw or "[]")
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [x for x in parsed if isinstance(x, dict)]


@router.get("/statistics")
async def invoice_registry_statistics(
    year: str = Query("2026"),
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_bearer_user),
):
    if year != "2026":
        raise HTTPException(status_code=400, detail="Statistics v1 currently supports only year=2026")
    repo = InvoiceRegistryRepository(session)
    rows = await repo.list_2026_rows()
    by_currency: dict[str, float] = {}
    partner_currency: dict[str, dict[str, float]] = {}
    for row in rows:
        amount = _parse_amount(row.amount or "")
        if amount is None:
            continue
        cur = _normalize_currency(row.currency or "")
        if not cur:
            continue
        by_currency[cur] = by_currency.get(cur, 0.0) + amount
        partner = _normalize_partner(row.partner or "")
        if not partner:
            continue
        if partner not in partner_currency:
            partner_currency[partner] = {}
        partner_currency[partner][cur] = partner_currency[partner].get(cur, 0.0) + amount
    invoiced_by_currency = [
        {"currency": c, "invoiced": round(v, 2)}
        for c, v in sorted(by_currency.items(), key=lambda item: (-item[1], item[0]))
    ]
    currencies = [item["currency"] for item in invoiced_by_currency]
    partners = []
    for partner, amounts in sorted(
        partner_currency.items(),
        key=lambda item: (-max(item[1].values()) if item[1] else 0.0, item[0]),
    ):
        partners.append(
            {
                "partner": partner,
                "amounts": {cur: round(float(amounts.get(cur, 0.0)), 2) for cur in currencies},
            }
        )
    return {
        "year": "2026",
        "invoicedByCurrency": invoiced_by_currency,
        "partnerMatrix": {"currencies": currencies, "partners": partners},
    }


@router.get("/{year_id}")
async def get_invoice_registry_sheet(
    year_id: str,
    q: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_bearer_user),
):
    repo = InvoiceRegistryRepository(session)
    if year_id == "2026":
        rows = await repo.list_2026_rows(q=q)
        return {
            "year": "2026",
            "sheetName": SHEET_NAMES["2026"],
            "mode": "active",
            "columns": COL_2026,
            "rows": [_row_to_payload(r) for r in rows],
            "statuses": sorted(CANON_STATUSES),
        }
    if year_id not in ARCHIVE_YEARS:
        raise HTTPException(status_code=404, detail="Unknown sheet")
    archive = await repo.get_archive_sheet(year_id)
    rows = _safe_rows_json(archive.rows_json if archive else "[]")
    return {
        "year": year_id,
        "sheetName": SHEET_NAMES.get(year_id, year_id),
        "mode": "archive",
        "columns": [],
        "rows": rows,
        "statuses": sorted(CANON_STATUSES),
    }


@router.post("/2026/rows", status_code=201)
async def create_invoice_registry_row_2026(
    body: RegistryRowBody,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_bearer_user),
):
    repo = InvoiceRegistryRepository(session)
    created = await repo.create_2026_row(body.model_dump(), updated_by=int(user.get("id") or 0))
    await session.commit()
    return _row_to_payload(created)


@router.patch("/2026/rows/{row_id}")
async def patch_invoice_registry_row_2026(
    row_id: str,
    body: dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_bearer_user),
):
    repo = InvoiceRegistryRepository(session)
    row = await repo.get_2026_row(row_id)
    if not row:
        raise HTTPException(status_code=404, detail="Row not found")
    patched = await repo.patch_2026_row(row, body, updated_by=int(user.get("id") or 0))
    await session.commit()
    return _row_to_payload(patched)


@router.put("/2026/rows/{row_id}")
async def put_invoice_registry_row_2026(
    row_id: str,
    body: RegistryRowBody,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_bearer_user),
):
    repo = InvoiceRegistryRepository(session)
    row = await repo.get_2026_row(row_id)
    if not row:
        raise HTTPException(status_code=404, detail="Row not found")
    data = body.model_dump()
    data["id"] = row_id
    patched = await repo.patch_2026_row(row, data, updated_by=int(user.get("id") or 0))
    await session.commit()
    return _row_to_payload(patched)


@router.delete("/2026/rows/{row_id}", status_code=204)
async def delete_invoice_registry_row_2026(
    row_id: str,
    session: AsyncSession = Depends(get_session),
    _: dict = Depends(require_bearer_user),
):
    repo = InvoiceRegistryRepository(session)
    deleted = await repo.delete_2026_row(row_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Row not found")
    await session.commit()
    return None


@router.put("/2026/rows")
async def replace_invoice_registry_rows_2026(
    body: RegistryRowsReplaceBody,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(require_bearer_user),
):
    repo = InvoiceRegistryRepository(session)
    n = await repo.replace_2026_rows([r.model_dump() for r in body.rows], updated_by=int(user.get("id") or 0))
    await session.commit()
    return {"ok": True, "rows": n}

