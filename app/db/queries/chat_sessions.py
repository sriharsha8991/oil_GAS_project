"""Persistent chat-session index (the ChatGPT-style sidebar).

The conversation STM (rolling messages, summary, sticky subject) still
lives in Redis db2 with its TTL. This module persists only the *index*
the user sees and manipulates from the sidebar.

Temp / incognito chats never touch this table.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatSessionRow


async def ensure_session(
    sess: AsyncSession,
    *,
    session_id: str,
    user_id: str,
    project_id: str | None = None,
) -> ChatSessionRow:
    """Insert the row on first turn or bump activity counters on later turns.

    Returns the live row. ``message_count`` is incremented by one user-turn
    + one assistant-turn (=2) to match how the FE counts messages.
    """
    sid = uuid.UUID(session_id)
    stmt = pg_insert(ChatSessionRow).values(
        id=sid,
        user_id=user_id,
        project_id=project_id,
        message_count=2,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[ChatSessionRow.id],
        set_={
            "message_count": ChatSessionRow.message_count + 2,
            "last_message_at": func.now(),
            "updated_at": func.now(),
        },
    )
    await sess.execute(stmt)
    await sess.commit()
    row = (await sess.execute(
        select(ChatSessionRow).where(ChatSessionRow.id == sid)
    )).scalar_one()
    return row


async def set_title(
    sess: AsyncSession,
    *,
    session_id: str,
    user_id: str,
    title: str,
    clear_pending: bool = True,
) -> bool:
    """Owner-scoped title update. Returns True if a row was updated."""
    title = (title or "").strip()
    if not title:
        return False
    title = title[:120]
    values: dict[str, Any] = {"title": title, "updated_at": func.now()}
    if clear_pending:
        values["title_pending"] = False
    res = await sess.execute(
        update(ChatSessionRow)
        .where(
            ChatSessionRow.id == uuid.UUID(session_id),
            ChatSessionRow.user_id == user_id,
        )
        .values(**values)
    )
    await sess.commit()
    return (res.rowcount or 0) > 0


async def list_sessions(
    sess: AsyncSession,
    *,
    user_id: str,
    project_id: str | None = None,
    project_scope: str = "all",
    include_archived: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Sidebar list, most recently active first.

    - ``project_scope="all"`` (default): no project filter — every session
      the user owns. Use this for the global sidebar.
    - ``project_scope="current"``: filter to ``project_id`` exactly
      (NULL means the personal/global tier).
    """
    q = select(ChatSessionRow).where(ChatSessionRow.user_id == user_id)
    if project_scope == "current":
        if project_id is None:
            q = q.where(ChatSessionRow.project_id.is_(None))
        else:
            q = q.where(ChatSessionRow.project_id == uuid.UUID(project_id))
    if not include_archived:
        q = q.where(ChatSessionRow.archived_at.is_(None))
    q = q.order_by(ChatSessionRow.last_message_at.desc()).limit(limit)
    rows = (await sess.execute(q)).scalars().all()
    return [
        {
            "id": str(r.id),
            "title": r.title,
            "title_pending": r.title_pending,
            "project_id": str(r.project_id) if r.project_id else None,
            "message_count": r.message_count,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "last_message_at": (
                r.last_message_at.isoformat() if r.last_message_at else None
            ),
            "archived_at": r.archived_at.isoformat() if r.archived_at else None,
        }
        for r in rows
    ]


async def get_session_meta(
    sess: AsyncSession, *, session_id: str, user_id: str,
) -> ChatSessionRow | None:
    return (await sess.execute(
        select(ChatSessionRow).where(
            ChatSessionRow.id == uuid.UUID(session_id),
            ChatSessionRow.user_id == user_id,
        )
    )).scalar_one_or_none()


async def delete_session_row(
    sess: AsyncSession, *, session_id: str, user_id: str,
) -> int:
    res = await sess.execute(
        delete(ChatSessionRow).where(
            ChatSessionRow.id == uuid.UUID(session_id),
            ChatSessionRow.user_id == user_id,
        )
    )
    await sess.commit()
    return res.rowcount or 0


async def set_archived(
    sess: AsyncSession,
    *,
    session_id: str,
    user_id: str,
    archived: bool,
) -> bool:
    """Toggle ``archived_at``. Returns True if a row was updated."""
    values: dict[str, Any] = {
        "archived_at": func.now() if archived else None,
        "updated_at": func.now(),
    }
    res = await sess.execute(
        update(ChatSessionRow)
        .where(
            ChatSessionRow.id == uuid.UUID(session_id),
            ChatSessionRow.user_id == user_id,
        )
        .values(**values)
    )
    await sess.commit()
    return (res.rowcount or 0) > 0
