from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import Classification, Role
from app.infrastructure.database.base import Base, SoftDelete, Timestamps, UUIDPrimaryKey, str_enum


class RoleModel(Base):
    """Reference table; rows are seeded by the initial migration."""

    __tablename__ = "roles"

    name: Mapped[Role] = mapped_column(str_enum(Role, "role_name"), primary_key=True)
    description: Mapped[str] = mapped_column(String(200), nullable=False)


class User(UUIDPrimaryKey, Timestamps, SoftDelete, Base):
    __tablename__ = "users"
    __table_args__ = (
        # email unique among *live* users, case-insensitively; a deleted user's email can be reused
        Index(
            "uq_users_email_live", func.lower(text("email")), unique=True, postgresql_where=text("deleted_at IS NULL")
        ),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    clearance: Mapped[Classification] = mapped_column(
        str_enum(Classification, "clearance"), nullable=False, server_default=Classification.INTERNAL.value
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    roles: Mapped[list[UserRole]] = relationship(
        back_populates="user", lazy="selectin", foreign_keys="UserRole.user_id"
    )


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_name: Mapped[Role] = mapped_column(
        str_enum(Role, "user_role_name"), ForeignKey("roles.name"), primary_key=True
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    user: Mapped[User] = relationship(back_populates="roles", foreign_keys=[user_id])
