"""Long-term user memory queries.

Backed by ``public.user_memories`` (see migrations/0005_user_memories.sql).
All queries are owner-scoped; cross-user access is impossible because every
function takes a ``user_id`` and filters by it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MEMORY_SCOPES, UserMemory, UserProfile

# Memories below this confidence are not surfaced to the guard. Captures
# start at 0.5 by default and bump to >= 0.6 on second observation.
MIN_RECALL_CONFIDENCE = 0.5
MAX_RECALL_ITEMS = 8

# Scopes never surfaced to the LLM (kept for audit / undo).
HIDDEN_SCOPES = {"dismissed"}


# ---------------------------------------------------------------------------
# Capture / upsert
# ---------------------------------------------------------------------------

async def upsert_memory(
    sess: AsyncSession,
    *,
    user_id: str,
    scope: str,
    key: str,
    value: dict[str, Any] | str,
    project_id: str | None = None,
    source_msg: str | None = None,
    confidence_bump: float = 0.1,
) -> None:
    """Insert a new memory or strengthen an existing one.

    On collision (same user_id + project_id + scope + key) bumps
    ``confidence`` by ``confidence_bump`` (capped at 1.0) and overwrites
    ``value`` if it differs. ``project_id`` NULL = global/personal scope.
    """
    if scope not in MEMORY_SCOPES:
        raise ValueError(f"invalid scope: {scope}")
    if isinstance(value, str):
        value = {"text": value}

    stmt = pg_insert(UserMemory).values(
        user_id=user_id,
        project_id=project_id,
        scope=scope,
        key=key[:120],
        value=value,
        source_msg=(source_msg or "")[:500] or None,
        confidence=0.5,
    )
    stmt = stmt.on_conflict_do_update(
        # Matches the unique index user_memories_unique_scope_key
        # (user_id, project_id, scope, key) NULLS NOT DISTINCT.
        index_elements=[
            UserMemory.user_id, UserMemory.project_id,
            UserMemory.scope, UserMemory.key,
        ],
        set_={
            "value": stmt.excluded.value,
            "source_msg": stmt.excluded.source_msg,
            # SQL: least(1.0, confidence + bump)
            "confidence": (UserMemory.confidence + confidence_bump).self_group(),
            "updated_at": stmt.excluded.updated_at,
        },
    )
    await sess.execute(stmt)
    # Cap confidence at 1.0 in a follow-up update — cheaper than a CASE
    # expression in the upsert and keeps the SQL portable.
    cap_stmt = (
        update(UserMemory)
        .where(
            UserMemory.user_id == user_id,
            UserMemory.scope == scope,
            UserMemory.key == key[:120],
            UserMemory.confidence > 1.0,
        )
        .values(confidence=1.0)
    )
    if project_id is None:
        cap_stmt = cap_stmt.where(UserMemory.project_id.is_(None))
    else:
        cap_stmt = cap_stmt.where(UserMemory.project_id == project_id)
    await sess.execute(cap_stmt)


# ---------------------------------------------------------------------------
# Recall
# ---------------------------------------------------------------------------

async def fetch_top_memories(
    sess: AsyncSession,
    *,
    user_id: str,
    project_id: str | None = None,
    max_items: int = MAX_RECALL_ITEMS,
    min_confidence: float = MIN_RECALL_CONFIDENCE,
) -> list[dict[str, Any]]:
    """Return the strongest, most recent memories for the LLM context card.

    Strict isolation: when ``project_id`` is set, only memories tagged
    with that exact project are returned. When ``project_id`` is None,
    only global (project_id IS NULL) memories are returned.
    """
    stmt = (
        select(
            UserMemory.scope,
            UserMemory.key,
            UserMemory.value,
            UserMemory.confidence,
            UserMemory.updated_at,
        )
        .where(
            UserMemory.user_id == user_id,
            UserMemory.confidence >= min_confidence,
            UserMemory.scope.notin_(HIDDEN_SCOPES),
        )
        .order_by(
            UserMemory.confidence.desc(),
            UserMemory.updated_at.desc(),
        )
        .limit(max_items)
    )
    if project_id is None:
        stmt = stmt.where(UserMemory.project_id.is_(None))
    else:
        stmt = stmt.where(UserMemory.project_id == project_id)
    rows = (await sess.execute(stmt)).all()
    return [
        {
            "scope": r.scope,
            "key": r.key,
            "value": r.value,
            "confidence": float(r.confidence),
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]


async def list_memories(
    sess: AsyncSession,
    *,
    user_id: str,
    project_id: str | None = None,
    project_scope: str = "current",
    scope: str | None = None,
) -> list[dict[str, Any]]:
    """Owner-only enumeration for the privacy drawer.

    ``project_scope`` controls multi-project visibility:
    - ``current`` (default): only memories matching ``project_id`` (which
      may be NULL for the personal/global tier).
    - ``all``: every memory the user owns (global + every project), used
      by the privacy/settings UI to show the full footprint.
    Excludes ``dismissed`` regardless.
    """
    stmt = (
        select(
            UserMemory.scope,
            UserMemory.key,
            UserMemory.value,
            UserMemory.confidence,
            UserMemory.project_id,
            UserMemory.created_at,
            UserMemory.updated_at,
        )
        .where(
            UserMemory.user_id == user_id,
            UserMemory.scope.notin_(HIDDEN_SCOPES),
        )
        .order_by(UserMemory.scope, UserMemory.updated_at.desc())
    )
    if project_scope == "current":
        if project_id is None:
            stmt = stmt.where(UserMemory.project_id.is_(None))
        else:
            stmt = stmt.where(UserMemory.project_id == project_id)
    if scope:
        if scope not in MEMORY_SCOPES or scope in HIDDEN_SCOPES:
            return []
        stmt = stmt.where(UserMemory.scope == scope)
    rows = (await sess.execute(stmt)).all()
    return [
        {
            "scope": r.scope,
            "key": r.key,
            "value": r.value,
            "confidence": float(r.confidence),
            "project_id": str(r.project_id) if r.project_id else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Forget
# ---------------------------------------------------------------------------

async def forget_memory(
    sess: AsyncSession,
    *,
    user_id: str,
    scope: str,
    key: str,
    project_id: str | None = None,
) -> bool:
    """Hard-delete a single memory (the user explicitly asked us to forget)."""
    if scope not in MEMORY_SCOPES or scope in HIDDEN_SCOPES:
        return False
    stmt = delete(UserMemory).where(
        UserMemory.user_id == user_id,
        UserMemory.scope == scope,
        UserMemory.key == key,
    )
    if project_id is None:
        stmt = stmt.where(UserMemory.project_id.is_(None))
    else:
        stmt = stmt.where(UserMemory.project_id == project_id)
    res = await sess.execute(stmt)
    return (res.rowcount or 0) > 0


async def forget_all_memories(
    sess: AsyncSession,
    *,
    user_id: str,
    project_id: str | None = None,
    project_scope: str = "current",
) -> int:
    """Hard-delete memories for the user.

    ``project_scope='current'`` (default) only deletes memories matching
    the ``project_id`` filter (NULL for global). ``project_scope='all'``
    nukes every memory the user owns across all projects — used by the
    privacy panel's "forget everything" button.
    """
    stmt = delete(UserMemory).where(UserMemory.user_id == user_id)
    if project_scope == "current":
        if project_id is None:
            stmt = stmt.where(UserMemory.project_id.is_(None))
        else:
            stmt = stmt.where(UserMemory.project_id == project_id)
    res = await sess.execute(stmt)
    return int(res.rowcount or 0)


# ---------------------------------------------------------------------------
# Memory toggle (per-user preference)
# ---------------------------------------------------------------------------

async def get_memory_enabled(
    sess: AsyncSession, *, user_id: str
) -> bool:
    """Return the user's cross-session memory toggle. Defaults true if missing."""
    row = (
        await sess.execute(
            select(UserProfile.memory_enabled).where(UserProfile.id == user_id)
        )
    ).scalar_one_or_none()
    return True if row is None else bool(row)


async def set_memory_enabled(
    sess: AsyncSession, *, user_id: str, enabled: bool
) -> bool:
    """Update the toggle. Returns the new value (or current if no row)."""
    res = await sess.execute(
        update(UserProfile)
        .where(UserProfile.id == user_id)
        .values(memory_enabled=enabled)
        .returning(UserProfile.memory_enabled)
    )
    row = res.scalar_one_or_none()
    return bool(row) if row is not None else enabled
