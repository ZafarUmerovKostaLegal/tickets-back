from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from infrastructure.models import (
    KOSTA_DAILY_SLUG,
    MESSAGE_KIND_POLL,
    MESSAGE_KIND_QUIZ,
    POLL_KIND_QUIZ,
    ROOM_TYPE_CHANNEL,
    ROOM_TYPE_COMPANY,
    MEMBER_ROLE_ADMIN,
)


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


class PollOptionOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    index: int
    text: str
    votes: int = 0
    voter_ids: list[int] = Field(default_factory=list, alias="voterIds")


class PollOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    kind: str
    question: str
    options: list[PollOptionOut]
    allows_multiple: bool = Field(False, alias="allowsMultiple")
    is_anonymous: bool = Field(False, alias="isAnonymous")
    is_closed: bool = Field(False, alias="isClosed")
    correct_option_index: int | None = Field(None, alias="correctOptionIndex")
    explanation: str | None = None
    total_voters: int = Field(0, alias="totalVoters")
    my_votes: list[int] = Field(default_factory=list, alias="myVotes")


class MessageOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    room_id: int = Field(..., alias="roomId")
    author_user_id: int = Field(..., alias="authorUserId")
    message_kind: str = Field("text", alias="messageKind")
    body: str
    created_at: datetime = Field(..., alias="createdAt")
    edited_at: datetime | None = Field(None, alias="editedAt")
    is_deleted: bool = Field(False, alias="isDeleted")
    attachments: list[AttachmentOut] = Field(default_factory=list)
    reply_to: ReplyToOut | None = Field(None, alias="replyTo")
    reactions: list[ReactionCountOut] = Field(default_factory=list)
    poll: PollOut | None = None


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
    is_channel: bool = Field(False, alias="isChannel")
    can_post: bool = Field(True, alias="canPost")


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


class CreateChannelRoomBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=500)
    member_user_ids: list[int] = Field(default_factory=list, alias="memberUserIds")


class CreatePollBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: str = Field("poll", pattern="^(poll|quiz)$")
    question: str = Field(..., min_length=1, max_length=500)
    options: list[str] = Field(..., min_length=2, max_length=10)
    allows_multiple: bool = Field(False, alias="allowsMultiple")
    is_anonymous: bool = Field(False, alias="isAnonymous")
    correct_option_index: int | None = Field(None, alias="correctOptionIndex")
    explanation: str | None = Field(None, max_length=1000)
    reply_to_message_id: int | None = Field(None, alias="replyToMessageId")

    @field_validator("options")
    @classmethod
    def _strip_options(cls, v: list[str]) -> list[str]:
        cleaned = [o.strip() for o in v if o and o.strip()]
        if len(cleaned) < 2:
            raise ValueError("At least 2 non-empty options required")
        return cleaned[:10]


class VotePollBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    option_index: int = Field(..., ge=0, alias="optionIndex")


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


def _parse_poll_options_json(raw: str) -> list[str]:
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def poll_to_out(poll, votes: list, *, viewer_id: int | None = None) -> PollOut:
    options_text = _parse_poll_options_json(poll.options_json)
    counts: dict[int, list[int]] = {i: [] for i in range(len(options_text))}
    for v in votes:
        idx = int(v.option_index)
        if 0 <= idx < len(options_text):
            if poll.is_anonymous:
                counts[idx].append(-1)
            else:
                counts[idx].append(int(v.user_id))
    my_votes: list[int] = []
    if viewer_id is not None:
        my_votes = sorted({int(v.option_index) for v in votes if int(v.user_id) == int(viewer_id)})
    voter_sets = {i: {u for u in uids if u >= 0} for i, uids in counts.items()}
    show_correct = poll.kind == POLL_KIND_QUIZ and (poll.is_closed or bool(my_votes))
    return PollOut(
        id=poll.id,
        kind=poll.kind,
        question=poll.question,
        options=[
            PollOptionOut(
                index=i,
                text=text,
                votes=len(voter_sets.get(i, set())),
                voter_ids=[] if poll.is_anonymous else sorted(voter_sets.get(i, set())),
            )
            for i, text in enumerate(options_text)
        ],
        allows_multiple=poll.allows_multiple,
        is_anonymous=poll.is_anonymous,
        is_closed=poll.is_closed,
        correct_option_index=poll.correct_option_index if show_correct else None,
        explanation=poll.explanation if show_correct and my_votes else None,
        total_voters=len({int(v.user_id) for v in votes}),
        my_votes=my_votes,
    )


def message_to_out(
    msg,
    attachments=None,
    *,
    reply_to: ReplyToOut | None = None,
    reactions: list | None = None,
    poll=None,
    poll_votes: list | None = None,
    viewer_id: int | None = None,
) -> MessageOut:
    deleted = msg.deleted_at is not None
    atts = [] if deleted else [attachment_to_out(a) for a in (attachments or [])]
    poll_out = None
    if poll and not deleted:
        poll_out = poll_to_out(poll, poll_votes or [], viewer_id=viewer_id)
    kind = getattr(msg, "message_kind", None) or "text"
    return MessageOut(
        id=msg.id,
        room_id=msg.room_id,
        author_user_id=msg.author_user_id,
        message_kind=kind,
        body=msg.body if not deleted else "",
        created_at=msg.created_at,
        edited_at=msg.edited_at,
        is_deleted=deleted,
        attachments=atts,
        reply_to=reply_to,
        reactions=reactions_to_out(reactions or []),
        poll=poll_out,
    )


def messages_to_out_list(
    items,
    atts_by_msg: dict,
    replies_by_id: dict,
    reactions_by_msg: dict | None = None,
    polls_by_msg: dict | None = None,
    votes_by_poll: dict | None = None,
    viewer_id: int | None = None,
) -> list[MessageOut]:
    rxmap = reactions_by_msg or {}
    pmap = polls_by_msg or {}
    vmap = votes_by_poll or {}
    out: list[MessageOut] = []
    for m in items:
        reply_out = None
        rid = getattr(m, "reply_to_message_id", None)
        if rid is not None:
            parent = replies_by_id.get(int(rid))
            if parent is not None:
                reply_out = reply_to_out(parent)
        poll = pmap.get(m.id)
        poll_votes = vmap.get(poll.id, []) if poll else []
        out.append(message_to_out(
            m,
            atts_by_msg.get(m.id),
            reply_to=reply_out,
            reactions=rxmap.get(m.id),
            poll=poll,
            poll_votes=poll_votes,
            viewer_id=viewer_id,
        ))
    return out


def _room_can_post(room, member_role: str) -> bool:
    if room.room_type == ROOM_TYPE_CHANNEL:
        return member_role == MEMBER_ROLE_ADMIN
    return True


def room_to_out(row, *, polls_by_msg: dict | None = None, votes_by_poll: dict | None = None, viewer_id: int | None = None) -> RoomOut:
    room = row.room
    last = row.last_message
    last_out = None
    if last:
        poll = (polls_by_msg or {}).get(last.id)
        poll_votes = (votes_by_poll or {}).get(poll.id, []) if poll else []
        last_out = message_to_out(
            last,
            reply_to=None,
            poll=poll,
            poll_votes=poll_votes,
            viewer_id=viewer_id,
        )
    is_channel = room.room_type in (ROOM_TYPE_COMPANY, ROOM_TYPE_CHANNEL)
    return RoomOut(
        id=room.id,
        slug=room.slug,
        title=room.title,
        room_type=room.room_type,
        my_role=row.member_role,
        last_message=last_out,
        unread_count=row.unread_count,
        is_company_channel=room.slug == KOSTA_DAILY_SLUG,
        is_channel=is_channel,
        can_post=_room_can_post(room, row.member_role),
    )
