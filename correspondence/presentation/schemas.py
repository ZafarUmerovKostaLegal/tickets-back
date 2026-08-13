from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class UserSnippetOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    display_name: Optional[str] = Field(None, serialization_alias="displayName")
    email: Optional[str] = None
    picture: Optional[str] = None
    position: Optional[str] = None


class AttachmentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    file_name: str = Field(serialization_alias="fileName")
    content_type: Optional[str] = Field(None, serialization_alias="contentType")
    size_bytes: int = Field(serialization_alias="sizeBytes")
    attachment_kind: str = Field(serialization_alias="attachmentKind")
    created_at: datetime = Field(serialization_alias="createdAt")


class DocumentListItemOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    registry_number: Optional[str] = Field(None, serialization_alias="registryNumber")
    direction: str
    counterparty: str
    subject: str
    doc_type: str = Field(serialization_alias="docType")
    status: str
    registered_at: Optional[datetime] = Field(None, serialization_alias="registeredAt")
    responsible_user_id: int = Field(serialization_alias="responsibleUserId")
    responsible_user: Optional[UserSnippetOut] = Field(None, serialization_alias="responsibleUser")
    partner_user_id: Optional[int] = Field(None, serialization_alias="partnerUserId")
    partner_user: Optional[UserSnippetOut] = Field(None, serialization_alias="partnerUser")
    attachments_count: int = Field(0, serialization_alias="attachmentsCount")
    has_scan: bool = Field(False, serialization_alias="hasScan")
    comment: Optional[str] = None
    rejection_comment: Optional[str] = Field(None, serialization_alias="rejectionComment")
    created_at: Optional[datetime] = Field(None, serialization_alias="createdAt")


class DocumentDetailOut(DocumentListItemOut):
    attachments: list[AttachmentOut] = Field(default_factory=list)


class DocumentListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[DocumentListItemOut]
    total: int
    skip: int
    limit: int


class StatsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    incoming_total: int = Field(serialization_alias="incomingTotal")
    outgoing_total: int = Field(serialization_alias="outgoingTotal")
    approval_total: int = Field(serialization_alias="approvalTotal")
    incoming_new_total: int = Field(serialization_alias="incomingNewTotal")
    partner_attention_total: int = Field(0, serialization_alias="partnerAttentionTotal")
    partner_outgoing_pending: int = Field(0, serialization_alias="partnerOutgoingPending")
    partner_incoming_new: int = Field(0, serialization_alias="partnerIncomingNew")


class DocumentPatchBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Optional[str] = None
    responsible_user_id: Optional[int] = Field(None, validation_alias="responsibleUserId")
    comment: Optional[str] = None
    counterparty: Optional[str] = None
    subject: Optional[str] = None
    partner_user_id: Optional[int] = Field(None, validation_alias="partnerUserId")


class SubmitReviewBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    partner_user_id: int = Field(..., validation_alias="partnerUserId")


class RejectReviewBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    comment: str = Field(..., min_length=1)


class CreateCommentBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    body: str = Field(..., min_length=1, max_length=4000)


class CommentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    body: str
    author_user_id: int = Field(serialization_alias="authorUserId")
    author_user: Optional[UserSnippetOut] = Field(None, serialization_alias="authorUser")
    created_at: datetime = Field(serialization_alias="createdAt")


class CommentListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[CommentOut]
