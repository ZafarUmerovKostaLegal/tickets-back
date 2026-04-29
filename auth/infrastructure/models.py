from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Boolean, ForeignKey, UniqueConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from infrastructure.database import Base


class LocalAdminCredentialModel(Base):


    __tablename__ = "local_admin_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class RoleModel(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class RolePermissionModel(Base):
    __tablename__ = "role_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True)
    permission_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (UniqueConstraint("role_id", "permission_key", name="uq_role_permissions_role_key"),)


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    azure_oid: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    picture: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="Сотрудник")
    position: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    time_tracking_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    desktop_background: Mapped[str | None] = mapped_column(String(512), nullable=True)
    active_session_jti: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
