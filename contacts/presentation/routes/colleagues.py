from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException

from infrastructure.upstream import auth_get, tt_json
from presentation.dependencies import is_hidden_system_user, require_colleagues_access
from presentation.schemas import ColleagueOut, normalize_colleague

router = APIRouter(prefix="/colleagues", tags=["colleagues"])


def _employee_label(row: ColleagueOut) -> str:
    name = (row.display_name or "").strip()
    if name:
        return name
    return row.email.strip() or f"User {row.id}"


def _unwrap_user_list(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        for key in ("items", "data"):
            val = raw.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


@router.get("", response_model=list[ColleagueOut])
async def list_colleagues(
    user: dict = Depends(require_colleagues_access),
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
):
    auth_header = (authorization or "").strip()
    tt_rows_raw = await tt_json("GET", "/users", authorization=auth_header)
    tt_rows = _unwrap_user_list(tt_rows_raw)

    auth_rows: list[dict[str, Any]] = []
    try:
        auth_raw = await auth_get("/users?include_archived=false", authorization=auth_header)
        auth_rows = _unwrap_user_list(auth_raw)
    except HTTPException as exc:
        if exc.status_code != 403:
            raise

    by_id: dict[int, ColleagueOut] = {}
    for raw in tt_rows:
        row = normalize_colleague(raw)
        if not row or row.is_archived or row.is_blocked or is_hidden_system_user(raw):
            continue
        by_id[row.id] = row

    for raw in auth_rows:
        if is_hidden_system_user(raw):
            continue
        row = normalize_colleague(raw)
        if not row or row.is_archived or row.is_blocked:
            continue
        if row.id not in by_id:
            by_id[row.id] = row

    out = sorted(by_id.values(), key=_employee_label)
    return out
