from typing import Optional
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile, Query
import httpx
from infrastructure.auth_upstream import verify_bearer_and_get_user
from infrastructure.config import get_settings

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])

ROLES_CAN_WRITE = {"IT отдел", "Администратор", "Офис менеджер", "Партнер"}


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    return await verify_bearer_and_get_user(request, authorization)


def require_write_role(user: dict = Depends(get_current_user)):
    if (user.get("role") or "").strip() not in ROLES_CAN_WRITE:
        raise HTTPException(
            status_code=403,
            detail="Only IT, Administrator, Office manager and Partner can create, edit or assign inventory.",
        )
    return user


def _inventory_url(path: str) -> str:
    base = get_settings().inventory_service_url.rstrip("/")
    return f"{base}{path}"


async def _proxy_get(path: str, params: Optional[dict] = None):
    url = _inventory_url(path)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url, params=params)
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail="Inventory service unavailable") from e
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Inventory service error")
    return r.json()


async def _proxy_post_json(path: str, json: dict, user: dict = Depends(require_write_role)):
    url = _inventory_url(path)
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(url, json=json)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Inventory service error")
    return r.json()


async def _proxy_patch(path: str, json: dict, user: dict = Depends(require_write_role)):
    url = _inventory_url(path)
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.patch(url, json=json)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Inventory service error")
    return r.json()


async def _proxy_delete(path: str, user: dict = Depends(require_write_role)):
    url = _inventory_url(path)
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.delete(url)
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Not found")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Inventory service error")
    if r.status_code == 204:
        return None
    return r.json()


@router.get("/categories")
async def list_categories(user: dict = Depends(get_current_user)):
    return await _proxy_get("/categories")


@router.get("/categories/{category_id}")
async def get_category(category_id: int, user: dict = Depends(get_current_user)):
    data = await _proxy_get(f"/categories/{category_id}")
    return data


@router.post("/categories", status_code=201)
async def create_category(body: dict, user: dict = Depends(require_write_role)):
    url = _inventory_url("/categories")
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(url, json=body)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Inventory service error")
    return r.json()


@router.patch("/categories/{category_id}")
async def update_category(category_id: int, body: dict, user: dict = Depends(require_write_role)):
    url = _inventory_url(f"/categories/{category_id}")
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.patch(url, json=body)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Inventory service error")
    return r.json()


@router.delete("/categories/{category_id}", status_code=204)
async def delete_category(category_id: int, user: dict = Depends(require_write_role)):
    await _proxy_delete(f"/categories/{category_id}")


@router.get("/items/statuses")
async def list_item_statuses(user: dict = Depends(get_current_user)):
    return await _proxy_get("/items/statuses")


@router.get("/items")
async def list_items(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    category_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    assigned_to_user_id: Optional[int] = Query(None),
    include_archived: bool = Query(False),
    user: dict = Depends(get_current_user),
):
    params = {"skip": skip, "limit": limit, "include_archived": include_archived}
    if category_id is not None:
        params["category_id"] = category_id
    if status is not None:
        params["status"] = status
    if assigned_to_user_id is not None:
        params["assigned_to_user_id"] = assigned_to_user_id
    return await _proxy_get("/items", params=params)


@router.get("/items/{item_uuid}")
async def get_item(item_uuid: str, user: dict = Depends(get_current_user)):
    return await _proxy_get(f"/items/{item_uuid}")


@router.post("/items", status_code=201)
async def create_item(
    name: str = Form(...),
    category_id: int = Form(...),
    inventory_number: str = Form(...),
    description: Optional[str] = Form(None),
    serial_number: Optional[str] = Form(None),
    status: str = Form("in_stock"),
    purchase_date: Optional[str] = Form(None),
    warranty_until: Optional[str] = Form(None),
    photo: Optional[UploadFile] = File(None),
    user: dict = Depends(require_write_role),
):
    settings = get_settings()
    form_data = {
        "name": name,
        "category_id": category_id,
        "inventory_number": inventory_number,
        "description": description or "",
        "serial_number": serial_number or "",
        "status": status,
        "purchase_date": purchase_date or "",
        "warranty_until": warranty_until or "",
    }
    files = []
    if photo and photo.filename:
        content = await photo.read()
        files = [("photo", (photo.filename, content, photo.content_type or "application/octet-stream"))]
    url = f"{settings.inventory_service_url}/items"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, data=form_data, files=files if files else None)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Inventory service error")
    return r.json()


@router.patch("/items/{item_uuid}")
async def update_item(item_uuid: str, body: dict, user: dict = Depends(require_write_role)):
    url = _inventory_url(f"/items/{item_uuid}")
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.patch(url, json=body)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Inventory service error")
    return r.json()


@router.post("/items/{item_uuid}/photo")
async def upload_item_photo(
    item_uuid: str,
    photo: UploadFile = File(...),
    user: dict = Depends(require_write_role),
):
    url = _inventory_url(f"/items/{item_uuid}/photo")
    content = await photo.read()
    files = [("photo", (photo.filename or "photo", content, photo.content_type or "application/octet-stream"))]
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, files=files)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Inventory service error")
    return r.json()


@router.post("/items/{item_uuid}/assign")
async def assign_item(item_uuid: str, body: dict, user: dict = Depends(require_write_role)):
    url = _inventory_url(f"/items/{item_uuid}/assign")
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(url, json=body)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Inventory service error")
    return r.json()


@router.post("/items/{item_uuid}/unassign")
async def unassign_item(item_uuid: str, user: dict = Depends(require_write_role)):
    url = _inventory_url(f"/items/{item_uuid}/unassign")
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(url)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Inventory service error")
    return r.json()


@router.patch("/items/{item_uuid}/archive")
async def archive_item(
    item_uuid: str,
    is_archived: bool = True,
    user: dict = Depends(require_write_role),
):
    url = _inventory_url(f"/items/{item_uuid}/archive")
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.patch(url, params={"is_archived": is_archived})
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Inventory service error")
    return r.json()


@router.delete("/items/{item_uuid}", status_code=204)
async def delete_item(item_uuid: str, user: dict = Depends(require_write_role)):
    await _proxy_delete(f"/items/{item_uuid}")
