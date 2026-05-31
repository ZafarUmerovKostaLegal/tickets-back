from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from starlette.responses import Response

from infrastructure.upstream import tt_json, tt_request
from presentation.dependencies import require_client_contacts_manage, require_client_contacts_view
from presentation.schemas import ClientContactCreateBody, ClientContactOut, ClientContactPatchBody

router = APIRouter(prefix="/clients", tags=["client_contacts"])


def _auth_header(authorization: str | None) -> str:
    auth = (authorization or "").strip()
    if not auth:
        raise HTTPException(status_code=401, detail="Authorization required")
    return auth


def _dump_body(body, *, exclude_unset: bool = False) -> dict:
    return json.loads(body.model_dump_json(by_alias=False, exclude_unset=exclude_unset))


@router.get("")
async def list_clients(
    include_archived: bool = Query(False, alias="includeArchived"),
    limit: int | None = Query(None, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: dict = Depends(require_client_contacts_view),
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
):
    params: dict[str, str] = {"includeArchived": "true" if include_archived else "false"}
    if limit is not None:
        params["limit"] = str(limit)
        params["offset"] = str(offset)
    return await tt_json("GET", "/clients", authorization=_auth_header(authorization), params=params)


@router.get("/{client_id}/contacts", response_model=list[ClientContactOut])
async def list_client_contacts(
    client_id: str,
    _: dict = Depends(require_client_contacts_view),
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
):
    raw = await tt_json("GET", f"/clients/{client_id}/contacts", authorization=_auth_header(authorization))
    if not isinstance(raw, list):
        return []
    return [ClientContactOut.model_validate(x) for x in raw if isinstance(x, dict)]


@router.get("/{client_id}/contacts/{contact_id}", response_model=ClientContactOut)
async def get_client_contact(
    client_id: str,
    contact_id: str,
    _: dict = Depends(require_client_contacts_view),
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
):
    raw = await tt_json(
        "GET",
        f"/clients/{client_id}/contacts/{contact_id}",
        authorization=_auth_header(authorization),
    )
    return ClientContactOut.model_validate(raw)


@router.post("/{client_id}/contacts", response_model=ClientContactOut)
async def create_client_contact(
    client_id: str,
    body: ClientContactCreateBody,
    _: dict = Depends(require_client_contacts_manage),
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    payload = _dump_body(body)
    payload["name"] = name
    raw = await tt_json(
        "POST",
        f"/clients/{client_id}/contacts",
        authorization=_auth_header(authorization),
        json_body=payload,
    )
    return ClientContactOut.model_validate(raw)


@router.patch("/{client_id}/contacts/{contact_id}", response_model=ClientContactOut)
async def patch_client_contact(
    client_id: str,
    contact_id: str,
    body: ClientContactPatchBody,
    _: dict = Depends(require_client_contacts_manage),
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
):
    payload = _dump_body(body, exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "name" in payload and not str(payload["name"] or "").strip():
        raise HTTPException(status_code=400, detail="name cannot be empty")
    raw = await tt_json(
        "PATCH",
        f"/clients/{client_id}/contacts/{contact_id}",
        authorization=_auth_header(authorization),
        json_body=payload,
    )
    return ClientContactOut.model_validate(raw)


@router.delete("/{client_id}/contacts/{contact_id}", status_code=204)
async def delete_client_contact(
    client_id: str,
    contact_id: str,
    _: dict = Depends(require_client_contacts_manage),
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
):
    r = await tt_request(
        "DELETE",
        f"/clients/{client_id}/contacts/{contact_id}",
        authorization=_auth_header(authorization),
    )
    if r.status_code >= 400:
        try:
            detail: Any = r.json()
        except Exception:
            detail = r.text or "Time tracking service error"
        raise HTTPException(status_code=r.status_code, detail=detail)
    return Response(status_code=204)
