from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TicketResponse(BaseModel):
    id: int
    uuid: str
    theme: str
    description: str
    attachment_path: Optional[str]
    status: str
    created_by_user_id: int
    created_at: datetime
    category: str
    priority: str
    is_archived: bool = False
    partner_user_id: Optional[int] = None
    rejection_comment: Optional[str] = None


class TicketUpdateRequest(BaseModel):
    theme: Optional[str] = None
    description: Optional[str] = None
    attachment_path: Optional[str] = None
    status: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    partner_user_id: Optional[int] = None
    rejection_comment: Optional[str] = None
    clear_partner_user_id: bool = False
    clear_rejection_comment: bool = False


class TicketSubmitApprovalRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    partner_user_id: int = Field(..., alias="partnerUserId", gt=0)


class TicketRejectApprovalRequest(BaseModel):
    comment: str = Field(..., min_length=1)


class StatusItem(BaseModel):
    value: str
    label: str


class PriorityItem(BaseModel):
    value: str
    label: str


class CommentResponse(BaseModel):
    id: int
    ticket_id: int
    user_id: int
    content: str
    created_at: datetime
    updated_at: datetime


class CommentCreateRequest(BaseModel):
    content: str


class CommentUpdateRequest(BaseModel):
    content: str
