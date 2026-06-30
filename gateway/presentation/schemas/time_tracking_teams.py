
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TimeTrackingTeamCreateBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=255)
    partner_auth_user_id: int = Field(..., alias="partnerAuthUserId")
    member_auth_user_ids: list[int] = Field(default_factory=list, alias="memberAuthUserIds")


class TimeTrackingTeamPatchBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    partner_auth_user_id: Optional[int] = Field(None, alias="partnerAuthUserId")
    member_auth_user_ids: Optional[list[int]] = Field(None, alias="memberAuthUserIds")
    is_archived: Optional[bool] = Field(None, alias="isArchived")
