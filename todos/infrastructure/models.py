

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.database import Base


BOARD_VIS_PERSONAL = "personal"
BOARD_VIS_SHARED = "shared"

# Заголовки по умолчанию: первая автосозданная доска vs явно созданная через POST /boards
TODO_BOARD_TITLE_PRIMARY_DEFAULT = "Моя доска"
TODO_BOARD_TITLE_NEW_DEFAULT = "Новая доска"
BOARD_ROLES = frozenset({"owner", "editor", "viewer"})
INVITE_PENDING = "pending"
INVITE_ACCEPTED = "accepted"
INVITE_DECLINED = "declined"
INVITE_REVOKED = "revoked"
INVITE_EXPIRED = "expired"


class OutlookCalendarTokenModel(Base):


    __tablename__ = "outlook_calendar_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TodoBoardModel(Base):


    __tablename__ = "todo_boards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
        doc="Владелец доски (auth_user_id)",
    )
    title: Mapped[str] = mapped_column(
        String(200), nullable=False, default=TODO_BOARD_TITLE_PRIMARY_DEFAULT
    )
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default=BOARD_VIS_PERSONAL)
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    background_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    columns: Mapped[list["TodoColumnModel"]] = relationship(
        "TodoColumnModel",
        back_populates="board",
        cascade="all, delete-orphan",
    )
    labels: Mapped[list["TodoBoardLabelModel"]] = relationship(
        "TodoBoardLabelModel",
        back_populates="board",
        cascade="all, delete-orphan",
    )
    members: Mapped[list["TodoBoardMemberModel"]] = relationship(
        "TodoBoardMemberModel",
        back_populates="board",
        cascade="all, delete-orphan",
    )
    invites: Mapped[list["TodoBoardInviteModel"]] = relationship(
        "TodoBoardInviteModel",
        back_populates="board",
        cascade="all, delete-orphan",
    )


class TodoBoardMemberModel(Base):


    __tablename__ = "todo_board_members"

    board_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("todo_boards.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="editor")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    board: Mapped["TodoBoardModel"] = relationship("TodoBoardModel", back_populates="members")


class TodoBoardInviteModel(Base):


    __tablename__ = "todo_board_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    board_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("todo_boards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    inviter_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    invitee_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    role_offered: Mapped[str] = mapped_column(String(32), nullable=False, default="editor")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=INVITE_PENDING)
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    board: Mapped["TodoBoardModel"] = relationship("TodoBoardModel", back_populates="invites")


class TodoBoardLabelModel(Base):


    __tablename__ = "todo_board_labels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    board_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("todo_boards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    color: Mapped[str] = mapped_column(String(32), nullable=False, default="#6b7280")
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    board: Mapped["TodoBoardModel"] = relationship("TodoBoardModel", back_populates="labels")
    card_links: Mapped[list["TodoCardLabelModel"]] = relationship(
        "TodoCardLabelModel",
        back_populates="label",
        cascade="all, delete-orphan",
    )


class TodoColumnModel(Base):


    __tablename__ = "todo_board_columns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    board_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("todo_boards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    color: Mapped[str] = mapped_column(String(32), nullable=False, default="#6b7280")
    is_collapsed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    board: Mapped["TodoBoardModel"] = relationship("TodoBoardModel", back_populates="columns")
    cards: Mapped[list["TodoCardModel"]] = relationship(
        "TodoCardModel",
        back_populates="column",
        cascade="all, delete-orphan",
    )


class TodoCardModel(Base):


    __tablename__ = "todo_board_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    column_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("todo_board_columns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    column: Mapped["TodoColumnModel"] = relationship("TodoColumnModel", back_populates="cards")
    label_links: Mapped[list["TodoCardLabelModel"]] = relationship(
        "TodoCardLabelModel",
        back_populates="card",
        cascade="all, delete-orphan",
    )
    checklist_items: Mapped[list["TodoCardChecklistItemModel"]] = relationship(
        "TodoCardChecklistItemModel",
        back_populates="card",
        cascade="all, delete-orphan",
    )
    participants: Mapped[list["TodoCardParticipantModel"]] = relationship(
        "TodoCardParticipantModel",
        back_populates="card",
        cascade="all, delete-orphan",
    )
    attachments: Mapped[list["TodoCardAttachmentModel"]] = relationship(
        "TodoCardAttachmentModel",
        back_populates="card",
        cascade="all, delete-orphan",
    )
    comments: Mapped[list["TodoCardCommentModel"]] = relationship(
        "TodoCardCommentModel",
        back_populates="card",
        cascade="all, delete-orphan",
    )


class TodoCardLabelModel(Base):


    __tablename__ = "todo_card_labels"

    card_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("todo_board_cards.id", ondelete="CASCADE"),
        primary_key=True,
    )
    label_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("todo_board_labels.id", ondelete="CASCADE"),
        primary_key=True,
    )

    card: Mapped["TodoCardModel"] = relationship("TodoCardModel", back_populates="label_links")
    label: Mapped["TodoBoardLabelModel"] = relationship("TodoBoardLabelModel", back_populates="card_links")


class TodoCardChecklistItemModel(Base):


    __tablename__ = "todo_card_checklist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("todo_board_cards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    is_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    card: Mapped["TodoCardModel"] = relationship("TodoCardModel", back_populates="checklist_items")


class TodoCardParticipantModel(Base):


    __tablename__ = "todo_card_participants"

    card_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("todo_board_cards.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)

    card: Mapped["TodoCardModel"] = relationship("TodoCardModel", back_populates="participants")


class TodoCardAttachmentModel(Base):


    __tablename__ = "todo_card_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("todo_board_cards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    card: Mapped["TodoCardModel"] = relationship("TodoCardModel", back_populates="attachments")


class TodoCardCommentModel(Base):


    __tablename__ = "todo_card_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("todo_board_cards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    card: Mapped["TodoCardModel"] = relationship("TodoCardModel", back_populates="comments")
