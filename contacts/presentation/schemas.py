from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, Field
from pydantic import BaseModel, ConfigDict


class ClientContactOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    phone: str | None = None
    email: str | None = None
    sort_order: int | None = Field(None, validation_alias=AliasChoices("sortOrder", "sort_order"))


class ClientContactCreateBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    phone: str | None = None
    email: str | None = None
    sort_order: int | None = Field(None, validation_alias=AliasChoices("sortOrder", "sort_order"))


class ClientContactPatchBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    phone: str | None = None
    email: str | None = None
    sort_order: int | None = Field(None, validation_alias=AliasChoices("sortOrder", "sort_order"))


class ColleagueOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    email: str
    display_name: str | None = Field(None, validation_alias=AliasChoices("displayName", "display_name"))
    picture: str | None = None
    role: str | None = None
    position: str | None = None
    is_blocked: bool = Field(False, validation_alias=AliasChoices("isBlocked", "is_blocked"))
    is_archived: bool = Field(False, validation_alias=AliasChoices("isArchived", "is_archived"))


def normalize_colleague(raw: dict[str, Any]) -> ColleagueOut | None:
    uid = raw.get("id") or raw.get("auth_user_id") or raw.get("authUserId")
    try:
        user_id = int(uid)
    except (TypeError, ValueError):
        return None
    if user_id <= 0:
        return None
    email = str(raw.get("email") or "").strip()
    return ColleagueOut(
        id=user_id,
        email=email,
        display_name=raw.get("display_name") or raw.get("displayName"),
        picture=raw.get("picture"),
        role=raw.get("role"),
        position=raw.get("position") or raw.get("job_title") or raw.get("jobTitle"),
        is_blocked=bool(raw.get("is_blocked") or raw.get("isBlocked")),
        is_archived=bool(raw.get("is_archived") or raw.get("isArchived")),
    )
