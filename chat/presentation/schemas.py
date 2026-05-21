from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from infrastructure.models import KOSTA_DAILY_SLUG


class MessageOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    room_id: int = Field(..., alias="roomId")
    author_user_id: int = Field(..., alias="authorUserId")
    body: str
    created_at: datetime = Field(..., alias="createdAt")
    edited_at: datetime | None = Field(None, alias="editedAt")
    is_deleted: bool = Field(False, alias="isDeleted")


class RoomMemberOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: int = Field(..., alias="userId")
    role: str
    joined_at: datetime = Field(..., alias="joinedAt")


class RoomOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    slug: str | None = None
    title: str
    room_type: str = Field(..., alias="roomType")
    my_role: str = Field(..., alias="myRole")
    last_message: MessageOut | None = Field(None, alias="lastMessage")
    unread_count: int = Field(0, alias="unreadCount")
    is_company_channel: bool = Field(False, alias="isCompanyChannel")


class RoomsListOut(BaseModel):
    items: list[RoomOut]


class MessagesListOut(BaseModel):
    items: list[MessageOut]
    has_more: bool = Field(False, alias="hasMore")


class PostMessageBody(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)


class PatchMessageBody(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)


class MarkReadBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message_id: int | None = Field(None, alias="messageId")


class CreateGroupRoomBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., min_length=1, max_length=200)
    member_user_ids: list[int] = Field(default_factory=list, alias="memberUserIds")


class CreateDmRoomBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    other_user_id: int = Field(..., alias="otherUserId")


class AddRoomMembersBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_ids: list[int] = Field(..., min_length=1, alias="userIds")


class RoomMembersListOut(BaseModel):
    items: list[RoomMemberOut]


def message_to_out(msg) -> MessageOut:
    return MessageOut(
        id=msg.id,
        room_id=msg.room_id,
        author_user_id=msg.author_user_id,
        body=msg.body if msg.deleted_at is None else "",
        created_at=msg.created_at,
        edited_at=msg.edited_at,
        is_deleted=msg.deleted_at is not None,
    )


def room_to_out(row) -> RoomOut:
    room = row.room
    last = row.last_message
    return RoomOut(
        id=room.id,
        slug=room.slug,
        title=room.title,
        room_type=room.room_type,
        my_role=row.member_role,
        last_message=message_to_out(last) if last else None,
        unread_count=row.unread_count,
        is_company_channel=room.slug == KOSTA_DAILY_SLUG,
    )
