import uuid
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile

from infrastructure.auth_upstream import (
    access_token_from_request,
    auth_service_request,
    verify_bearer_and_get_user,
)
from infrastructure.config import get_settings
from presentation.schemas.user_schemas import (
    UserResponse,
    UserDetailResponse,
    SetRoleRequest,
    BlockUserRequest,
    ArchiveUserRequest,
    TimeTrackingRoleRequest,
    SetPositionRequest,
    WeeklyCapacityPatchBody,
)
from presentation.time_tracking_capacity import fetch_weekly_capacity_hours, merge_weekly_capacity_into_user

router = APIRouter(prefix="/api/v1/users", tags=["users"])

MAIN_ADMIN_ROLE = "Главный администратор"
ADMIN_ROLE = "Администратор"
PARTNER_ROLE = "Партнер"
IT_ROLE = "IT отдел"
OFFICE_MANAGER_ROLE = "Офис менеджер"
OFFICE_MANAGER_ROLE_ALT = "Офис-менеджер"

ROLES_CAN_VIEW_USERS = {
    MAIN_ADMIN_ROLE,
    ADMIN_ROLE,
    PARTNER_ROLE,
    IT_ROLE,
    OFFICE_MANAGER_ROLE,
    OFFICE_MANAGER_ROLE_ALT,
}
ROLES_CAN_MANAGE_USERS = {MAIN_ADMIN_ROLE, ADMIN_ROLE, PARTNER_ROLE}


def bearer_for_upstream(request: Request, authorization: Optional[str]) -> Optional[str]:
    tok = access_token_from_request(request, authorization)
    return f"Bearer {tok}" if tok else None


async def _get_current_user_optional(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> dict:
    return await verify_bearer_and_get_user(request, authorization)


async def require_auth(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):

    return await _get_current_user_optional(request, authorization)


async def require_admin(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):

    user = await _get_current_user_optional(request, authorization)
    role = (user.get("role") or "").strip()
    if role not in ROLES_CAN_MANAGE_USERS:
        raise HTTPException(
            status_code=403,
            detail="Only Main Administrator, Administrator or Partner can manage users",
        )
    return user


async def require_main_admin(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):

    user = await _get_current_user_optional(request, authorization)
    role = (user.get("role") or "").strip()
    if role != MAIN_ADMIN_ROLE:
        raise HTTPException(
            status_code=403,
            detail="Only Main Administrator can assign user roles",
        )
    return user


async def require_main_admin_or_administrator(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):

    user = await _get_current_user_optional(request, authorization)
    role = (user.get("role") or "").strip()
    if role not in (MAIN_ADMIN_ROLE, ADMIN_ROLE):
        raise HTTPException(
            status_code=403,
            detail="Only Main Administrator or Administrator can assign user roles",
        )
    return user


async def require_admin_or_it(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):

    user = await _get_current_user_optional(request, authorization)
    role = (user.get("role") or "").strip()
    if role not in ROLES_CAN_VIEW_USERS:
        raise HTTPException(
            status_code=403,
            detail="Only Main Administrator, Administrator, Partner, IT department or Office manager can view user details",
        )
    return user


async def verify_user_detail_access(
    user_id: int,
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):

    user = await verify_bearer_and_get_user(request, authorization)
    role = (user.get("role") or "").strip()
    rid = user.get("id")
    if rid is not None and int(rid) == int(user_id):
        return user
    if role not in ROLES_CAN_VIEW_USERS:
        raise HTTPException(
            status_code=403,
            detail="Only Main Administrator, Administrator, Partner, IT department or Office manager can view other users' profiles",
        )
    return user


def _image_magic_matches_content(content: bytes, ext: str) -> bool:
    if len(content) < 12:
        return False
    e = (ext or "").lower()
    if e in (".jpg", ".jpeg"):
        return content[:3] == b"\xff\xd8\xff"
    if e == ".png":
        return content[:8] == b"\x89PNG\r\n\x1a\n"
    if e == ".gif":
        return content[:6] in (b"GIF87a", b"GIF89a")
    if e == ".webp":
        return content[:4] == b"RIFF" and len(content) >= 12 and content[8:12] == b"WEBP"
    return False


DESKTOP_BG_MAX_MB = 5
DESKTOP_BG_ALLOWED = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
DESKTOP_BG_SUBDIR = "desktop_backgrounds"


@router.get("/me", response_model=UserResponse)
async def get_me(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    user = await verify_bearer_and_get_user(request, authorization)
    return await merge_weekly_capacity_into_user(user, bearer_for_upstream(request, authorization))


@router.post("/me/desktop-background", response_model=UserResponse)
async def upload_desktop_background(
    request: Request,
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(require_auth),
):

    user = await _get_current_user_optional(request, authorization)
    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not found")

    ext = (Path(file.filename or "").suffix or "").lower()
    if ext not in DESKTOP_BG_ALLOWED:
        raise HTTPException(
            status_code=400,
            detail=f"Недопустимый формат. Разрешены: {', '.join(sorted(DESKTOP_BG_ALLOWED))}",
        )

    content = await file.read()
    if len(content) > DESKTOP_BG_MAX_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Файл превышает {DESKTOP_BG_MAX_MB} МБ")
    if not _image_magic_matches_content(content, ext):
        raise HTTPException(
            status_code=400,
            detail="Содержимое файла не совпадает с разрешённым форматом изображения",
        )

    settings = get_settings()
    base_dir = Path(settings.media_path).resolve()

    old_path = user.get("desktop_background")
    if old_path:
        old_file = (base_dir / old_path).resolve()
        if str(old_file).startswith(str(base_dir)) and old_file.exists() and old_file.is_file():
            old_file.unlink(missing_ok=True)
    user_dir = base_dir / DESKTOP_BG_SUBDIR / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)

    unique_name = f"{uuid.uuid4().hex}{ext}"
    file_path = user_dir / unique_name
    file_path.write_bytes(content)

    rel_path = f"{DESKTOP_BG_SUBDIR}/{user_id}/{unique_name}"

    r = await auth_service_request(
        "PATCH",
        "/users/me/desktop-background",
        bearer_for_upstream(request, authorization),
        timeout=10.0,
        json={"path": rel_path},
    )
    if r.status_code != 200:
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=r.status_code, detail=r.text or "Failed to save settings")
    return await merge_weekly_capacity_into_user(r.json(), bearer_for_upstream(request, authorization))


@router.delete("/me/desktop-background", response_model=UserResponse)
async def delete_desktop_background(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(require_auth),
):

    user = await _get_current_user_optional(request, authorization)
    user_id = user.get("id")
    old_path = user.get("desktop_background")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not found")

    settings = get_settings()
    base_dir = Path(settings.media_path).resolve()
    if old_path:
        target = (base_dir / old_path).resolve()
        if str(target).startswith(str(base_dir)) and target.exists() and target.is_file():
            target.unlink(missing_ok=True)

    r = await auth_service_request(
        "DELETE",
        "/users/me/desktop-background",
        bearer_for_upstream(request, authorization),
        timeout=10.0,
    )
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text or "Failed to delete")
    return await merge_weekly_capacity_into_user(r.json(), bearer_for_upstream(request, authorization))


@router.get("", response_model=list[UserResponse])
async def list_users(
    request: Request,
    include_archived: bool = Query(False, description="Include archived users"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(require_admin_or_it),
):
    r = await auth_service_request(
        "GET",
        "/users",
        bearer_for_upstream(request, authorization),
        params={"include_archived": include_archived},
    )
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code == 403:
        try:
            d = r.json().get("detail", "Forbidden")
        except Exception:
            d = "Forbidden"
        raise HTTPException(status_code=403, detail=d)
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    return r.json()


@router.patch("/me/weekly-capacity-hours", response_model=UserResponse)
async def patch_me_weekly_capacity(
    request: Request,
    body: WeeklyCapacityPatchBody,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(require_auth),
):

    user = await verify_bearer_and_get_user(request, authorization)
    uid = user.get("id")
    if not uid:
        raise HTTPException(status_code=401, detail="User not found")
    settings = get_settings()
    base = (settings.time_tracking_service_url or "").strip().rstrip("/")
    if not base:
        raise HTTPException(status_code=503, detail="Time tracking service not configured")
    hours = float(body.weekly_capacity_hours)
    au = bearer_for_upstream(request, authorization)
    auth_headers = {"Authorization": au} if au else {}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{base}/users/{uid}", headers=auth_headers)
            if r.status_code == 200:
                r2 = await client.patch(
                    f"{base}/users/{uid}/weekly-capacity-hours",
                    json={"weekly_capacity_hours": hours},
                    headers=auth_headers,
                )
            elif r.status_code == 404:
                tt_role = (
                    (user.get("time_tracking_role") or user.get("timeTrackingRole") or "") or ""
                ).strip()
                pos = user.get("position")
                pos_s = str(pos).strip() if pos is not None and str(pos).strip() else None
                if tt_role in ("user", "manager") and not pos_s:
                    raise HTTPException(
                        status_code=400,
                        detail="Для нормы часов в учёте времени у сотрудника должна быть указана должность.",
                    )
                r2 = await client.post(
                    f"{base}/users",
                    json={
                        "auth_user_id": uid,
                        "email": user["email"],
                        "display_name": user.get("display_name"),
                        "picture": user.get("picture"),
                        "position": pos_s,
                        "role": tt_role,
                        "is_blocked": user.get("is_blocked", False),
                        "is_archived": user.get("is_archived", False),
                        "weekly_capacity_hours": hours,
                    },
                    headers=auth_headers,
                )
            else:
                raise HTTPException(status_code=503, detail="Time tracking service error")
            if r2.status_code >= 400:
                detail = (r2.text or "Time tracking error")[:500]
                raise HTTPException(status_code=r2.status_code, detail=detail)
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Time tracking service unavailable")
    user["weekly_capacity_hours"] = hours
    return user


@router.get("/{user_id}", response_model=UserDetailResponse)
async def get_user_detail(
    request: Request,
    user_id: int,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(verify_user_detail_access),
):
    r = await auth_service_request("GET", f"/users/{user_id}", bearer_for_upstream(request, authorization))
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code == 403:
        try:
            d = r.json().get("detail", "Forbidden")
        except Exception:
            d = "Forbidden"
        raise HTTPException(status_code=403, detail=d)
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="User not found")
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    detail = r.json()
    cap = await fetch_weekly_capacity_hours(user_id, bearer_for_upstream(request, authorization))
    detail["weekly_capacity_hours"] = cap
    return detail


@router.patch("/{user_id}/role", response_model=UserDetailResponse)
async def set_user_role(
    request: Request,
    user_id: int,
    body: SetRoleRequest,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(require_main_admin_or_administrator),
):
    r = await auth_service_request(
        "PATCH",
        f"/users/{user_id}/role",
        bearer_for_upstream(request, authorization),
        json=body.model_dump(),
    )
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="User not found")
    if r.status_code == 403:
        try:
            detail = r.json().get("detail", "Forbidden")
        except Exception:
            detail = "Forbidden"
        raise HTTPException(status_code=403, detail=detail)
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    return r.json()


@router.patch("/{user_id}/block", response_model=UserDetailResponse)
async def block_user(
    request: Request,
    user_id: int,
    body: BlockUserRequest,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(require_admin),
):
    r = await auth_service_request(
        "PATCH",
        f"/users/{user_id}/block",
        bearer_for_upstream(request, authorization),
        json=body.model_dump(),
    )
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="User not found")
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    return r.json()


@router.patch("/{user_id}/archive", response_model=UserDetailResponse)
async def archive_user(
    request: Request,
    user_id: int,
    body: ArchiveUserRequest,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(require_admin),
):
    r = await auth_service_request(
        "PATCH",
        f"/users/{user_id}/archive",
        bearer_for_upstream(request, authorization),
        json=body.model_dump(),
    )
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="User not found")
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    return r.json()


@router.patch("/{user_id}/time-tracking-role", response_model=UserDetailResponse)
async def set_time_tracking_role(
    request: Request,
    user_id: int,
    body: TimeTrackingRoleRequest,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(require_admin),
):

    r = await auth_service_request(
        "PATCH",
        f"/users/{user_id}/time-tracking-role",
        bearer_for_upstream(request, authorization),
        json=body.model_dump(),
    )
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="User not found")
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    return r.json()


@router.patch("/{user_id}/position", response_model=UserDetailResponse)
async def set_position(
    request: Request,
    user_id: int,
    body: SetPositionRequest,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    _: dict = Depends(require_admin),
):

    r = await auth_service_request(
        "PATCH",
        f"/users/{user_id}/position",
        bearer_for_upstream(request, authorization),
        json=body.model_dump(),
    )
    if r.status_code == 401:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="User not found")
    if r.status_code >= 400:
        raise HTTPException(status_code=503, detail="Auth service error")
    return r.json()
