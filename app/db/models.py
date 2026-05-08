"""SQLAlchemy ORM models. PR-7: users_profile + projects + documents + jobs."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# Locked role enum — must match the SQL enum in migrations/0001_users_profile.sql
# + 0004_admin_role_and_quota.sql, and the frontend's role list.
ROLE_VALUES = (
    "super_admin",
    "admin",
    "drilling_engineer",
    "geologist",
    "asset_manager",
    "qa_steward",
)

# Roles permitted to invite users. 'admin' is org-scoped and quota-limited;
# 'super_admin' is unrestricted.
INVITER_ROLES = ("super_admin", "admin")


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
    organization: Mapped[str | None] = mapped_column(String, nullable=True)
    # Per-user toggle for cross-session (long-term) memory. When false, the
    # extract_user_memory worker no-ops and fetch_top_memories returns [].
    # Existing rows are kept (privacy auditing) but never surfaced.
    memory_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    # Max users this admin may invite. 0 = unlimited (default for super_admin
    # or unconfigured admins). Enforced in app layer at invite time.
    user_quota: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    # auth.users.id of the inviter (null for super_admin or pre-existing users).
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    current_phase: Mapped[str | None] = mapped_column(String, nullable=True)
    pages_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pages_done: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# Allowed scopes — must match the SQL CHECK constraint in
# migrations/0005_user_memories.sql.
MEMORY_SCOPES = ("preference", "fact", "project_pin", "style", "dismissed")


class UserMemory(Base):
    __tablename__ = "user_memories"

    # Surrogate PK (migration 0006). Logical uniqueness is enforced by the
    # composite unique index user_memories_unique_scope_key on
    # (user_id, project_id, scope, key) NULLS NOT DISTINCT.
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    # NULL == personal/global memory. Non-null == project-scoped, isolated.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    scope: Mapped[str] = mapped_column(String, nullable=False)
    key: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0.5", default=0.5
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChatSessionRow(Base):
    """ChatGPT-style sidebar row. The full STM (rolling messages, summary,
    sticky subject) still lives in Redis db2; this table is the durable
    index the user sees for listing/renaming/deleting their chats.

    Temp / incognito sessions are NEVER inserted here.
    """

    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(
        String, nullable=False, server_default="New chat", default="New chat"
    )
    title_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ChatMessageRow(Base):
    """Persistent chat transcript. One row per role-turn (user / assistant).

    Hydrates the conversation when the Redis STM has expired. Never
    written for temp / incognito chats.
    """

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
