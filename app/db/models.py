"""SQLAlchemy ORM models. PR-5: only `users_profile`."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# Locked role enum — must match the SQL enum in migrations/0001_users_profile.sql
# and the frontend's role list (FRONTEND_SPEC, ROLES_AND_DASHBOARDS).
ROLE_VALUES = (
    "super_admin",
    "drilling_engineer",
    "geologist",
    "asset_manager",
    "qa_steward",
)


class UserProfile(Base):
    __tablename__ = "users_profile"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(
        Enum(*ROLE_VALUES, name="user_role", create_type=False),
        nullable=False,
        default="drilling_engineer",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
