from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database import Base

ROOM_TYPE_COMPANY = "company"
ROOM_TYPE_GROUP = "group"
ROOM_TYPE_DM = "dm"

KOSTA_DAILY_SLUG = "kosta-daily"

MEMBER_ROLE_MEMBER = "member"
MEMBER_ROLE_ADMIN = "admin"


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


class ChatReadStateModel(Base):
    __tablename__ = "chat_read_state"

    room_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chat_rooms.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    last_read_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
