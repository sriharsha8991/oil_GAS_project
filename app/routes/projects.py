"""Projects route — CRUD for the user's project containers.

Projects are a per-user grouping of documents (one user, many projects;
one project, many documents). Deleting a project cascades through
documents → jobs in Postgres, and through Storage objects + Qdrant
points via explicit cleanup helpers.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, select

from app.auth.deps import AuthUser, current_user
from app.config import get_settings
from app.db.engine import get_session_factory
from app.db.models import Project
from app.storage.supabase_storage import SupabaseStorage
from app.utils.logging import get_logger
from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)
router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)


class ProjectUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)


class ProjectOut(BaseModel):
    id: str
    user_id: str
    name: str
    description: str | None = None
    created_at: str
    updated_at: str


def _serialize(p: Project) -> ProjectOut:
    return ProjectOut(
        id=str(p.id),
        user_id=str(p.user_id),
        name=p.name,
        description=p.description,
        created_at=p.created_at.isoformat(),
        updated_at=p.updated_at.isoformat(),
    )


@router.post("", response_model=ProjectOut, summary="Create a project")
async def create_project(
    body: ProjectCreate, user: AuthUser = Depends(current_user)
) -> ProjectOut:
    Session = get_session_factory()
    async with Session() as sess:
        row = Project(
            id=uuid.uuid4(),
            user_id=user.id,
            name=body.name,
            description=body.description,
        )
        sess.add(row)
        await sess.commit()
        await sess.refresh(row)
    return _serialize(row)


@router.get("", summary="List the calling user's projects")
async def list_projects(
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    Session = get_session_factory()
    async with Session() as sess:
        rows = (await sess.execute(
            select(Project)
            .where(Project.user_id == user.id)
            .order_by(desc(Project.created_at))
        )).scalars().all()
    return {"count": len(rows), "projects": [_serialize(r).model_dump() for r in rows]}


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: str, user: AuthUser = Depends(current_user)
) -> ProjectOut:
    Session = get_session_factory()
    async with Session() as sess:
        row = (await sess.execute(
            select(Project).where(
                Project.id == project_id, Project.user_id == user.id
            )
        )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    return _serialize(row)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    user: AuthUser = Depends(current_user),
) -> ProjectOut:
    Session = get_session_factory()
    async with Session() as sess:
        row = (await sess.execute(
            select(Project).where(
                Project.id == project_id, Project.user_id == user.id
            )
        )).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="project not found")
        if body.name is not None:
            row.name = body.name
        if body.description is not None:
            row.description = body.description
        row.updated_at = datetime.now(timezone.utc)
        await sess.commit()
        await sess.refresh(row)
    return _serialize(row)


@router.delete("/{project_id}", summary="Delete project + cascade")
async def delete_project(
    project_id: str,
    request: Request,
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    """Delete project, all its documents/jobs (DB cascade), Storage objects,
    and Qdrant points."""
    Session = get_session_factory()
    async with Session() as sess:
        row = (await sess.execute(
            select(Project).where(
                Project.id == project_id, Project.user_id == user.id
            )
        )).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="project not found")

    settings = get_settings()
    storage = SupabaseStorage(settings)
    vector_store: QdrantVectorStore | None = getattr(
        request.app.state, "qdrant", None
    )

    # Wipe Storage objects + vectors first; if either fails, leave DB row
    # so the caller can retry.
    await storage.delete_project(user.id, project_id)
    if vector_store is not None:
        await vector_store.delete_by_project(project_id)

    Session = get_session_factory()
    async with Session() as sess:
        await sess.execute(
            delete(Project).where(
                Project.id == project_id, Project.user_id == user.id
            )
        )
        await sess.commit()

    logger.info("project_deleted", user_id=user.id, project_id=project_id)
    return {"deleted": project_id}
