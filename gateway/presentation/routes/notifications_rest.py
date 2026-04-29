from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
import httpx
from backend_common.rbac_ui_permissions import NOTIFICATIONS_WRITE, role_in_set
from infrastructure.auth_upstream import verify_bearer_and_get_user
from infrastructure.config import get_settings
from infrastructure.upstream_auth_context import merge_upstream_headers

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    return await verify_bearer_and_get_user(request, authorization)


def require_write_role(user: dict = Depends(get_current_user)):
    role = (user.get("role") or "").strip()
    if not role_in_set(role, NOTIFICATIONS_WRITE):
        raise HTTPException(
            status_code=403,
            detail="Only Partner, IT department and Office manager can create, edit, archive or delete notifications.",
        )
    return user


def _notifications_url(path: str) -> str:
    base = get_settings().notifications_service_url.rstrip("/")
    return f"{base}/notifications{path}"


@router.get("")
async def list_notifications(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    include_archived: bool = False,
    user: dict = Depends(get_current_user),
):

    url = _notifications_url("")
    params = {"skip": skip, "limit": limit, "include_archived": include_archived}
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, params=params, headers=merge_upstream_headers())
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Notifications service error")
    return r.json()


@router.get("/{notification_uuid}")
async def get_notification(
    notification_uuid: str,
    user: dict = Depends(get_current_user),
):

    url = _notifications_url(f"/{notification_uuid}")
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url, headers=merge_upstream_headers())
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Notification not found")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Notifications service error")
    return r.json()


@router.post("", status_code=201)
async def create_notification(
    body: dict,
    user: dict = Depends(require_write_role),
):

    url = _notifications_url("")
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(url, json=body, headers=merge_upstream_headers())
    if r.status_code not in (200, 201):
        raise HTTPException(status_code=r.status_code, detail=r.text or "Notifications service error")
    return r.json()


@router.patch("/{notification_uuid}")
async def update_notification(
    notification_uuid: str,
    body: dict,
    user: dict = Depends(require_write_role),
):

    url = _notifications_url(f"/{notification_uuid}")
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.patch(url, json=body, headers=merge_upstream_headers())
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Notification not found")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Notifications service error")
    return r.json()


@router.post("/{notification_uuid}/archive")
async def archive_notification(
    notification_uuid: str,
    body: dict,
    user: dict = Depends(require_write_role),
):

    url = _notifications_url(f"/{notification_uuid}/archive")
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(url, json=body, headers=merge_upstream_headers())
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Notification not found")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Notifications service error")
    return r.json()


@router.delete("/{notification_uuid}", status_code=204)
async def delete_notification(
    notification_uuid: str,
    user: dict = Depends(require_write_role),
):

    url = _notifications_url(f"/{notification_uuid}")
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.delete(url, headers=merge_upstream_headers())
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Notification not found")
    if r.status_code != 204:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Notifications service error")
