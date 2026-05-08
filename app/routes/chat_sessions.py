"""Chat-session sidebar — list / rename / delete the user's chats.

The full conversation STM still lives in Redis db2; this surface manages
only the durable index row in ``public.chat_sessions``. Deleting a row
also drops the matching Redis key so the next visit starts clean.

All endpoints are owner-scoped via Supabase JWT. Temp / incognito chats
do not appear here because they are never indexed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.agents.rag.sessions import delete_session_async
from app.auth.deps import AuthUser, current_user
from app.db.engine import get_session_factory
from app.db.queries.chat_messages import list_messages
from app.db.queries.chat_sessions import (
    delete_session_row,
    get_session_meta,
    list_sessions,
    set_archived,
    set_title,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/chat/sessions", tags=["chat"])


class SessionItem(BaseModel):
    id: str
    title: str
    title_pending: bool
    project_id: str | None = None
    message_count: int
    created_at: str | None = None
    updated_at: str | None = None
    last_message_at: str | None = None
    archived_at: str | None = None


class SessionListResponse(BaseModel):
    items: list[SessionItem]


class RenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


class DeleteResponse(BaseModel):
    deleted: bool


class MessageItem(BaseModel):
    id: str
    role: str
    content: str
    sources: list | None = None
    meta: dict | None = None
    created_at: str | None = None


class MessageListResponse(BaseModel):
    items: list[MessageItem]


class ArchiveResponse(BaseModel):
    archived: bool


@router.get("", response_model=SessionListResponse, summary="List chat sessions")
async def list_chat_sessions(
    project_id: str | None = Query(None),
    project_scope: str = Query("all", pattern="^(all|current)$"),
    include_archived: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    user: AuthUser = Depends(current_user),
) -> SessionListResponse:
    Session = get_session_factory()
    async with Session() as sess:
        rows = await list_sessions(
            sess,
            user_id=user.id,
            project_id=project_id,
            project_scope=project_scope,
            include_archived=include_archived,
            limit=limit,
        )
    return SessionListResponse(items=[SessionItem(**r) for r in rows])


@router.patch("/{session_id}", response_model=SessionItem, summary="Rename a chat session")
async def rename_chat_session(
    session_id: str,
    body: RenameRequest,
    user: AuthUser = Depends(current_user),
) -> SessionItem:
    Session = get_session_factory()
    async with Session() as sess:
        ok = await set_title(
            sess,
            session_id=session_id,
            user_id=user.id,
            title=body.title,
            clear_pending=True,
        )
        if not ok:
            raise HTTPException(status_code=404, detail="session_not_found")
        row = await get_session_meta(
            sess, session_id=session_id, user_id=user.id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    return SessionItem(
        id=str(row.id),
        title=row.title,
        title_pending=row.title_pending,
        project_id=str(row.project_id) if row.project_id else None,
        message_count=row.message_count,
        created_at=row.created_at.isoformat() if row.created_at else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
        last_message_at=(
            row.last_message_at.isoformat() if row.last_message_at else None
        ),
        archived_at=row.archived_at.isoformat() if row.archived_at else None,
    )


@router.delete("/{session_id}", response_model=DeleteResponse, summary="Delete a chat session")
async def delete_chat_session(
    session_id: str,
    user: AuthUser = Depends(current_user),
) -> DeleteResponse:
    Session = get_session_factory()
    async with Session() as sess:
        n = await delete_session_row(
            sess, session_id=session_id, user_id=user.id,
        )
    if n == 0:
        raise HTTPException(status_code=404, detail="session_not_found")
    # Also drop the Redis STM blob so the conversation truly resets.
    try:
        await delete_session_async(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "chat_session_redis_delete_failed",
            session_id=session_id, error=str(exc),
        )
    return DeleteResponse(deleted=True)


@router.get(
    "/{session_id}/messages",
    response_model=MessageListResponse,
    summary="Hydrate the persisted message log for one session",
)
async def list_chat_session_messages(
    session_id: str,
    limit: int = Query(200, ge=1, le=1000),
    user: AuthUser = Depends(current_user),
) -> MessageListResponse:
    Session = get_session_factory()
    async with Session() as sess:
        meta = await get_session_meta(
            sess, session_id=session_id, user_id=user.id,
        )
        if meta is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        rows = await list_messages(
            sess, session_id=session_id, user_id=user.id, limit=limit,
        )
    return MessageListResponse(items=[MessageItem(**r) for r in rows])


@router.post(
    "/{session_id}/archive",
    response_model=ArchiveResponse,
    summary="Archive a chat session (hides it from the default sidebar)",
)
async def archive_chat_session(
    session_id: str,
    user: AuthUser = Depends(current_user),
) -> ArchiveResponse:
    Session = get_session_factory()
    async with Session() as sess:
        ok = await set_archived(
            sess, session_id=session_id, user_id=user.id, archived=True,
        )
    if not ok:
        raise HTTPException(status_code=404, detail="session_not_found")
    return ArchiveResponse(archived=True)


@router.post(
    "/{session_id}/unarchive",
    response_model=ArchiveResponse,
    summary="Restore an archived chat session",
)
async def unarchive_chat_session(
    session_id: str,
    user: AuthUser = Depends(current_user),
) -> ArchiveResponse:
    Session = get_session_factory()
    async with Session() as sess:
        ok = await set_archived(
            sess, session_id=session_id, user_id=user.id, archived=False,
        )
    if not ok:
        raise HTTPException(status_code=404, detail="session_not_found")
    return ArchiveResponse(archived=False)

