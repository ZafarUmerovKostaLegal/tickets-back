from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from infrastructure.models import KOSTA_DAILY_SLUG


class AttachmentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    file_name: str = Field(..., alias="fileName")
    content_type: str = Field(..., alias="contentType")
    size_bytes: int = Field(0, alias="sizeBytes")
    created_at: datetime = Field(..., alias="createdAt")


class ReactionCountOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    emoji: str
    count: int
    user_ids: list[int] = Field(default_factory=list, alias="userIds")


class ReplyToOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message_id: int = Field(..., alias="messageId")
    author_user_id: int = Field(..., alias="authorUserId")
    body: str = ""
    is_deleted: bool = Field(False, alias="isDeleted")


class MessageOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    room_id: int = Field(..., alias="roomId")
    author_user_id: int = Field(..., alias="authorUserId")
    body: str
    created_at: datetime = Field(..., alias="createdAt")
    edited_at: datetime | None = Field(None, alias="editedAt")
    is_deleted: bool = Field(False, alias="isDeleted")
    attachments: list[AttachmentOut] = Field(default_factory=list)
    reply_to: ReplyToOut | None = Field(None, alias="replyTo")
    reactions: list[ReactionCountOut] = Field(default_factory=list)


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


class ToggleReactionBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    emoji: str = Field(..., min_length=1, max_length=8)


class PostMessageBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    body: str = Field(..., min_length=1, max_length=4000)
    reply_to_message_id: int | None = Field(None, alias="replyToMessageId")


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


def attachment_to_out(att) -> AttachmentOut:
    return AttachmentOut(
        id=att.id,
        file_name=att.file_name,
        content_type=att.content_type,
        size_bytes=int(att.size_bytes or 0),
        created_at=att.created_at,
    )


def reply_to_out(msg) -> ReplyToOut:
    deleted = msg.deleted_at is not None
    body = "" if deleted else (msg.body or "")
    if len(body) > 500:
        body = body[:497] + "…"
    return ReplyToOut(
        message_id=msg.id,
        author_user_id=msg.author_user_id,
        body=body,
        is_deleted=deleted,
    )


def reactions_to_out(reactions: list) -> list[ReactionCountOut]:
    grouped: dict[str, list[int]] = {}
    for rx in reactions:
        grouped.setdefault(rx.emoji, []).append(rx.user_id)
    return [
        ReactionCountOut(emoji=emoji, count=len(uids), user_ids=uids)
        for emoji, uids in grouped.items()
    ]


def message_to_out(
    msg,
    attachments=None,
    *,
    reply_to: ReplyToOut | None = None,
    reactions: list | None = None,
) -> MessageOut:
    deleted = msg.deleted_at is not None
    atts = [] if deleted else [attachment_to_out(a) for a in (attachments or [])]
    return MessageOut(
        id=msg.id,
        room_id=msg.room_id,
        author_user_id=msg.author_user_id,
        body=msg.body if not deleted else "",
        created_at=msg.created_at,
        edited_at=msg.edited_at,
        is_deleted=deleted,
        attachments=atts,
        reply_to=reply_to,
        reactions=reactions_to_out(reactions or []),
    )


def messages_to_out_list(
    items,
    atts_by_msg: dict,
    replies_by_id: dict,
    reactions_by_msg: dict | None = None,
) -> list[MessageOut]:
    rxmap = reactions_by_msg or {}
    out: list[MessageOut] = []
    for m in items:
        reply_out = None
        rid = getattr(m, "reply_to_message_id", None)
        if rid is not None:
            parent = replies_by_id.get(int(rid))
            if parent is not None:
                reply_out = reply_to_out(parent)
        out.append(message_to_out(
            m,
            atts_by_msg.get(m.id),
            reply_to=reply_out,
            reactions=rxmap.get(m.id),
        ))
    return out


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
