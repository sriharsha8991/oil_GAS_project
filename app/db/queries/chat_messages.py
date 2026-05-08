"""Persistent chat-message transcript writes & reads."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatMessageRow


async def append_turn(
    sess: AsyncSession,
    *,
    session_id: str,
    user_id: str,
    user_message: str,
    assistant_message: str | None,
    sources: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Insert the user + assistant rows for one answered turn."""
    sid = uuid.UUID(session_id)
    sess.add(ChatMessageRow(
        session_id=sid, user_id=user_id,
        role="user", content=user_message or "",
    ))
    if assistant_message:
        sess.add(ChatMessageRow(
            session_id=sid, user_id=user_id,
            role="assistant", content=assistant_message,
            sources=sources, meta=meta,
        ))
    await sess.commit()


async def list_messages(
    sess: AsyncSession,
    *,
    session_id: str,
    user_id: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return messages for a session in chronological order, owner-scoped."""
    rows = (await sess.execute(
        select(ChatMessageRow)
        .where(
            ChatMessageRow.session_id == uuid.UUID(session_id),
            ChatMessageRow.user_id == user_id,
        )
        .order_by(ChatMessageRow.created_at.asc(), ChatMessageRow.id.asc())
        .limit(limit)
    )).scalars().all()
    return [
        {
            "id": str(r.id),
            "role": r.role,
            "content": r.content,
            "sources": r.sources,
            "meta": r.meta,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
