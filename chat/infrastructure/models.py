from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database import Base

ROOM_TYPE_COMPANY = "company"
ROOM_TYPE_GROUP = "group"
ROOM_TYPE_DM = "dm"
ROOM_TYPE_CHANNEL = "channel"

KOSTA_DAILY_SLUG = "kosta-daily"

MEMBER_ROLE_MEMBER = "member"
MEMBER_ROLE_ADMIN = "admin"

MESSAGE_KIND_TEXT = "text"
MESSAGE_KIND_POLL = "poll"
MESSAGE_KIND_QUIZ = "quiz"
MESSAGE_KIND_CHECKLIST = "checklist"

CHECKLIST_TITLE_MAX = 255
CHECKLIST_ITEM_MAX = 200
CHECKLIST_ITEMS_MAX = 30
PINNED_MESSAGES_MAX = 20

POLL_KIND_POLL = "poll"
POLL_KIND_QUIZ = "quiz"


class ChatRoomModel(Base):
    __tablename__ = "chat_rooms"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    slug: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    room_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    created_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dm_user_low: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dm_user_high: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("dm_user_low", "dm_user_high", name="uq_chat_dm_pair"),
    )


class ChatRoomMemberModel(Base):
    __tablename__ = "chat_room_members"

    room_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_rooms.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=MEMBER_ROLE_MEMBER)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatMessageModel(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_rooms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    message_kind: Mapped[str] = mapped_column(String(16), nullable=False, default=MESSAGE_KIND_TEXT, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    reply_to_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("chat_messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChatMessageAttachmentModel(Base):
    __tablename__ = "chat_message_attachments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


REACTION_MAX_EMOJI_LEN = 8


class ChatMessageReactionModel(Base):
    __tablename__ = "chat_message_reactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    emoji: Mapped[str] = mapped_column(String(REACTION_MAX_EMOJI_LEN), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("message_id", "user_id", "emoji", name="uq_chat_reaction_per_user"),
    )


class ChatPollModel(Base):
    __tablename__ = "chat_polls"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default=POLL_KIND_POLL)
    question: Mapped[str] = mapped_column(String(500), nullable=False)
    options_json: Mapped[str] = mapped_column(Text, nullable=False)
    allows_multiple: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_anonymous: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    correct_option_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    explanation: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatPollVoteModel(Base):
    __tablename__ = "chat_poll_votes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    poll_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_polls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    option_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("poll_id", "user_id", "option_index", name="uq_chat_poll_vote"),
    )


class ChatChecklistModel(Base):
    __tablename__ = "chat_checklists"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    title: Mapped[str] = mapped_column(String(CHECKLIST_TITLE_MAX), nullable=False)
    others_can_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    others_can_append: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatChecklistItemModel(Base):
    __tablename__ = "chat_checklist_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    checklist_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_checklists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(String(CHECKLIST_ITEM_MAX), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatChecklistCompletionModel(Base):
    __tablename__ = "chat_checklist_completions"

    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_checklist_items.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatPinnedMessageModel(Base):
    __tablename__ = "chat_pinned_messages"

    room_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_rooms.id", ondelete="CASCADE"), primary_key=True
    )
    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_messages.id", ondelete="CASCADE"), primary_key=True
    )
    pinned_by_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pinned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatPushSubscriptionModel(Base):
    __tablename__ = "chat_push_subscriptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    auth: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatReadStateModel(Base):
    __tablename__ = "chat_read_state"

    room_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_rooms.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    last_read_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
