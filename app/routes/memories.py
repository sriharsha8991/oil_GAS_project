"""Long-term user memory — list / forget / forget-all + cross-session toggle.

Every endpoint is owner-scoped via ``Depends(current_user)``. Memories
are personal and never cross-user. Project-scoped memories (project_id
NOT NULL) are isolated from the global tier (project_id IS NULL).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.deps import AuthUser, current_user
from app.db.engine import get_session_factory
from app.db.models import MEMORY_SCOPES, Project
from app.db.queries.memories import (
    forget_all_memories,
    forget_memory,
    get_memory_enabled,
    list_memories,
    set_memory_enabled,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/memories", tags=["memories"])


class MemoryItem(BaseModel):
    scope: str
    key: str
    value: Any
    confidence: float
    project_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MemoryListResponse(BaseModel):
    items: list[MemoryItem]


class ForgetResponse(BaseModel):
    deleted: int


class MemorySettings(BaseModel):
    memory_enabled: bool


class MemorySettingsUpdate(BaseModel):
    memory_enabled: bool


# ---------------------------------------------------------------------------
# Settings (cross-session memory toggle)
# ---------------------------------------------------------------------------


@router.get("/settings", summary="Get the caller's memory toggle",
            response_model=MemorySettings)
async def get_settings(
    user: AuthUser = Depends(current_user),
) -> MemorySettings:
    Session = get_session_factory()
    async with Session() as sess:
        enabled = await get_memory_enabled(sess, user_id=user.id)
    return MemorySettings(memory_enabled=enabled)


@router.patch("/settings", summary="Toggle cross-session memory on/off",
              response_model=MemorySettings)
async def update_settings(
    body: MemorySettingsUpdate,
    user: AuthUser = Depends(current_user),
) -> MemorySettings:
    Session = get_session_factory()
    async with Session() as sess:
        new_value = await set_memory_enabled(
            sess, user_id=user.id, enabled=body.memory_enabled,
        )
        await sess.commit()
    logger.info("memory_toggle_set",
                user_id=user.id, memory_enabled=new_value)
    return MemorySettings(memory_enabled=new_value)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _validate_project_ownership(
    sess, *, user_id: str, project_id: str,
) -> None:
    row = (
        await sess.execute(
            select(Project.id).where(
                Project.id == project_id, Project.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="project_not_found")


# ---------------------------------------------------------------------------
# Listing + forgetting
# ---------------------------------------------------------------------------


@router.get("", summary="List the caller's stored memories",
            response_model=MemoryListResponse)
async def list_my_memories(
    user: AuthUser = Depends(current_user),
    scope: str | None = Query(
        None,
        description=(
            "Optional scope filter. One of: "
            "preference, fact, project_pin, style."
        ),
    ),
    project_id: str | None = Query(
        None,
        description=(
            "Filter to a single project. Pair with project_scope=current "
            "(default) for strict isolation. Omit to see global memories "
            "(project_scope=current) or use project_scope=all to see every "
            "memory across all projects."
        ),
    ),
    project_scope: str = Query(
        "current",
        pattern="^(current|all)$",
        description=(
            "'current' (default): only the project scope identified by "
            "project_id (or global when project_id is omitted). "
            "'all': every memory the user owns across all projects."
        ),
    ),
) -> MemoryListResponse:
    if scope is not None and scope not in MEMORY_SCOPES:
        raise HTTPException(status_code=400, detail="invalid_scope")
    Session = get_session_factory()
    async with Session() as sess:
        if project_id and project_scope == "current":
            await _validate_project_ownership(
                sess, user_id=user.id, project_id=project_id,
            )
        rows = await list_memories(
            sess,
            user_id=user.id,
            scope=scope,
            project_id=project_id,
            project_scope=project_scope,
        )
    return MemoryListResponse(items=[MemoryItem(**r) for r in rows])


@router.delete("/{scope}/{key}", summary="Forget a single memory",
               response_model=ForgetResponse)
async def forget_one(
    scope: str,
    key: str,
    user: AuthUser = Depends(current_user),
    project_id: str | None = Query(
        None,
        description=(
            "If set, forget the memory in this project's scope; otherwise "
            "forget the global (personal) one. Project memories are isolated."
        ),
    ),
) -> ForgetResponse:
    if scope not in MEMORY_SCOPES or scope == "dismissed":
        raise HTTPException(status_code=400, detail="invalid_scope")
    Session = get_session_factory()
    async with Session() as sess:
        if project_id:
            await _validate_project_ownership(
                sess, user_id=user.id, project_id=project_id,
            )
        deleted = await forget_memory(
            sess, user_id=user.id, scope=scope, key=key,
            project_id=project_id,
        )
        if deleted:
            await sess.commit()
    logger.info("memory_deleted",
                user_id=user.id, scope=scope, key=key,
                project_id=project_id, found=deleted)
    return ForgetResponse(deleted=1 if deleted else 0)


@router.post("/forget-all", summary="Forget every memory for the caller",
             response_model=ForgetResponse)
async def forget_everything(
    user: AuthUser = Depends(current_user),
    project_id: str | None = Query(
        None,
        description=(
            "If set, only forget memories in this project's scope. "
            "If omitted with project_scope=current, only forget the global "
            "tier. Use project_scope=all to nuke every memory across "
            "every project."
        ),
    ),
    project_scope: str = Query(
        "current",
        pattern="^(current|all)$",
        description=(
            "'current' (default): only delete in the resolved scope. "
            "'all': delete every memory the user owns."
        ),
    ),
) -> ForgetResponse:
    Session = get_session_factory()
    async with Session() as sess:
        if project_id and project_scope == "current":
            await _validate_project_ownership(
                sess, user_id=user.id, project_id=project_id,
            )
        n = await forget_all_memories(
            sess, user_id=user.id,
            project_id=project_id, project_scope=project_scope,
        )
        if n:
            await sess.commit()
    logger.info("memory_deleted_all",
                user_id=user.id, project_id=project_id,
                project_scope=project_scope, count=n)
    return ForgetResponse(deleted=n)
