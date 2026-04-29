from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from infrastructure.auth_upstream import auth_service_request
from presentation.routes.users import require_admin

router = APIRouter(prefix="/api/v1/roles", tags=["roles"])


@router.get("")
async def list_roles(authorization: Optional[str] = Header(None, alias="Authorization"), _: dict = Depends(require_admin)):
    r = await auth_service_request("GET", "/roles", authorization, timeout=10.0)
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    return r.json()


@router.post("")
async def create_role(
    body: dict,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(require_admin),
):
    r = await auth_service_request("POST", "/roles", authorization, timeout=10.0, json=body)
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code == 400:
        raise HTTPException(status_code=400, detail=r.json().get("detail") or "Invalid role data")
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    return r.json()


@router.get("/{role_id}")
async def get_role(
    role_id: int,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(require_admin),
):
    r = await auth_service_request("GET", f"/roles/{role_id}", authorization, timeout=10.0)
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Role not found")
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    return r.json()


@router.patch("/{role_id}")
async def update_role(
    role_id: int,
    body: dict,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(require_admin),
):
    r = await auth_service_request("PATCH", f"/roles/{role_id}", authorization, timeout=10.0, json=body)
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code == 400:
        raise HTTPException(status_code=400, detail=r.json().get("detail") or "Invalid role data")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Role not found")
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    return r.json()


@router.delete("/{role_id}")
async def delete_role(
    role_id: int,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(require_admin),
):
    r = await auth_service_request("DELETE", f"/roles/{role_id}", authorization, timeout=10.0)
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Role not found")
    if r.status_code == 400:
        raise HTTPException(status_code=400, detail=r.json().get("detail") or "Cannot delete role")
    if r.status_code not in (200, 204):
        raise HTTPException(status_code=r.status_code, detail=r.text or "Role delete error")
    return {"ok": True}


@router.get("/{role_id}/permissions")
async def get_role_permissions(
    role_id: int,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(require_admin),
):
    r = await auth_service_request("GET", f"/roles/{role_id}/permissions", authorization, timeout=10.0)
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Role not found")
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    return r.json()


@router.patch("/{role_id}/permissions")
async def set_role_permissions(
    role_id: int,
    body: dict,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(require_admin),
):
    r = await auth_service_request(
        "PATCH",
        f"/roles/{role_id}/permissions",
        authorization,
        timeout=10.0,
        json=body,
    )
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Role not found")
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    return r.json()
