from __future__ import annotations

import json
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


class UserUpsertBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    auth_user_id: int = Field(..., alias="authUserId")
    email: str
    display_name: Optional[str] = Field(None, alias="displayName")
    picture: Optional[str] = None
    position: Optional[str] = None
    role: str = ""
    is_blocked: bool = Field(False, alias="isBlocked")
    is_archived: bool = Field(False, alias="isArchived")
    weekly_capacity_hours: Optional[Decimal] = Field(None, alias="weeklyCapacityHours")


def current_auth_user_id(user: dict) -> int:
    uid = user.get("id")
    if uid is None:
        raise HTTPException(status_code=403, detail="В токене нет id пользователя")
    return int(uid)


def user_payload_bool(user: dict, snake: str, camel: str) -> bool:
    v = user.get(snake)
    if v is not None:
        return v is True or v == 1 or str(v).lower() == "true"
    v = user.get(camel)
    if v is not None:
        return v is True or v == 1 or str(v).lower() == "true"
    return False


def _model_to_alias_free_dict(body: BaseModel) -> dict:
    return json.loads(body.model_dump_json(by_alias=False))


def build_self_time_tracking_user_upsert_payload(user: dict, body: UserUpsertBody) -> dict:
    my_id = current_auth_user_id(user)
    tt_auth_role = (user.get("time_tracking_role") or user.get("timeTrackingRole") or "").strip()
    if tt_auth_role not in {"user", "manager"}:
        raise HTTPException(
            status_code=403,
            detail="Нет роли в учёте времени (сотрудник или менеджер). Обратитесь к администратору организации.",
        )
    email = (str(user.get("email") or "").strip()) or (body.email or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="У пользователя нет email для синхронизации с учётом времени")

    disp = user.get("display_name")
    if disp is None:
        disp = user.get("displayName")
    if disp is None:
        display_name = body.display_name
    else:
        s = str(disp).strip()
        display_name = s if s else None

    pic = user.get("picture")
    if pic is None:
        picture = body.picture
    else:
        s = str(pic).strip()
        picture = s if s else None

    pos = user.get("position")
    if pos is not None and str(pos).strip():
        position = str(pos).strip()
    else:
        position = None

    if tt_auth_role in {"user", "manager"} and not position:
        raise HTTPException(
            status_code=400,
            detail="Для учёта времени у сотрудника должна быть указана должность. Обратитесь к администратору.",
        )

    safe = UserUpsertBody(
        auth_user_id=my_id,
        email=email,
        display_name=display_name,
        picture=picture,
        position=position,
        role=tt_auth_role,
        is_blocked=user_payload_bool(user, "is_blocked", "isBlocked"),
        is_archived=user_payload_bool(user, "is_archived", "isArchived"),
        weekly_capacity_hours=body.weekly_capacity_hours,
    )
    return _model_to_alias_free_dict(safe)
