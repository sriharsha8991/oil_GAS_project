"""Ingest routes — dispatch a PDF for processing.

Public endpoints
----------------
- ``POST   /ingest``                  JSON: ingest by server-side path
- ``POST   /ingest/upload``           multipart upload
- ``GET    /ingest``                  list this user's jobs
- ``GET    /ingest/{job_id}``         job status / summary

Per-document image, full-doc, and crop endpoints live in
``app.routes.documents`` (document_id-keyed, not job_id-keyed).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.auth.deps import AuthUser, current_user
from app.config import Settings, get_settings
from app.db.engine import get_session_factory
from app.db.models import Document, Job, Project
from app.storage.supabase_storage import SupabaseStorage
from app.utils.logging import get_logger

_MAX_BATCH_FILES = 20

logger = get_logger(__name__)
router = APIRouter(prefix="/ingest", tags=["ingest"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_pdf_path(raw_path: str, settings: Settings) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise HTTPException(status_code=400, detail=f"File not found: {raw_path}")
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {raw_path}")
    if resolved.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail=f"Not a PDF: {raw_path}")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    try:
        size = resolved.stat().st_size
    except OSError:
        raise HTTPException(status_code=400, detail=f"Cannot stat file: {raw_path}")
    if size > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {settings.max_upload_mb} MB limit: {raw_path}",
        )
    return resolved


async def _validate_project(project_id: str, user_id: str) -> None:
    Session = get_session_factory()
    async with Session() as sess:
        row = (await sess.execute(
            select(Project).where(
                Project.id == project_id,
                Project.user_id == user_id,
            )
        )).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="project not found")


async def _create_document_and_job(
    *, user_id: str, project_id: str, filename: str
) -> tuple[str, str]:
    """Insert documents + jobs rows and return (document_id, job_id)."""
    document_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    Session = get_session_factory()
    async with Session() as sess:
        sess.add(Document(
            id=document_id,
            user_id=user_id,
            project_id=project_id,
            filename=filename,
        ))
        sess.add(Job(
            id=job_id,
            document_id=document_id,
            user_id=user_id,
            status="queued",
            current_phase="queued",
        ))
        await sess.commit()
    return document_id, job_id


async def _dispatch(
    *,
    user: AuthUser,
    project_id: str,
    document_id: str,
    job_id: str,
    filename: str,
    pdf_bytes: bytes,
) -> None:
    settings = get_settings()
    storage = SupabaseStorage(settings)
    await storage.write_pdf(user.id, project_id, document_id, pdf_bytes)

    from app.workers.tasks.ingest import ingest_document

    doc_name = Path(filename).stem
    ingest_document.apply_async(
        args=[
            user.id,
            project_id,
            document_id,
            job_id,
            filename,
            user.role,
            user.organization,
            doc_name,
        ],
        task_id=job_id,
    )
    logger.info(
        "ingest_dispatched",
        user_id=user.id,
        project_id=project_id,
        document_id=document_id,
        job_id=job_id,
        filename=filename,
    )


# ---------------------------------------------------------------------------
# POST /ingest — JSON: ingest by server-side file path
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    project_id: str = Field(..., description="Project to attach documents to")
    file_paths: list[str] = Field(
        ...,
        min_length=1,
        max_length=_MAX_BATCH_FILES,
        description="Absolute or relative server-side paths to PDF files.",
    )


@router.post("", summary="Ingest one or more PDFs by path (async)")
async def ingest(
    req: IngestRequest, user: AuthUser = Depends(current_user)
) -> dict[str, Any]:
    settings = get_settings()
    await _validate_project(req.project_id, user.id)

    dispatched: list[dict[str, str]] = []
    for raw_path in req.file_paths:
        pdf_path = _resolve_pdf_path(raw_path, settings)
        filename = pdf_path.name
        try:
            data = pdf_path.read_bytes()
        except OSError as exc:
            raise HTTPException(
                status_code=400, detail=f"Cannot read file: {raw_path} ({exc})"
            )
        document_id, job_id = await _create_document_and_job(
            user_id=user.id, project_id=req.project_id, filename=filename
        )
        await _dispatch(
            user=user,
            project_id=req.project_id,
            document_id=document_id,
            job_id=job_id,
            filename=filename,
            pdf_bytes=data,
        )
        dispatched.append({
            "job_id": job_id,
            "document_id": document_id,
            "filename": filename,
            "status": "queued",
        })

    return {"total": len(dispatched), "jobs": dispatched}


# ---------------------------------------------------------------------------
# POST /ingest/upload — multipart upload
# ---------------------------------------------------------------------------


@router.post("/upload", summary="Upload PDFs and ingest them")
async def ingest_upload(
    project_id: str = Form(..., description="Project to attach documents to"),
    files: list[UploadFile] = File(...),
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    if len(files) > _MAX_BATCH_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"At most {_MAX_BATCH_FILES} files per request",
        )

    settings = get_settings()
    await _validate_project(project_id, user.id)
    max_bytes = settings.max_upload_mb * 1024 * 1024

    dispatched: list[dict[str, str]] = []
    for f in files:
        original = f.filename or "upload.pdf"
        if not original.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=400,
                detail=f"Only PDF files are accepted: {original}",
            )

        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = await f.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"File exceeds {settings.max_upload_mb} MB limit: "
                        f"{original}"
                    ),
                )
            chunks.append(chunk)
        await f.close()
        data = b"".join(chunks)

        document_id, job_id = await _create_document_and_job(
            user_id=user.id, project_id=project_id, filename=original
        )
        await _dispatch(
            user=user,
            project_id=project_id,
            document_id=document_id,
            job_id=job_id,
            filename=original,
            pdf_bytes=data,
        )
        dispatched.append({
            "job_id": job_id,
            "document_id": document_id,
            "filename": original,
            "size_bytes": str(size),
            "status": "queued",
        })

    return {"total": len(dispatched), "jobs": dispatched}


# ---------------------------------------------------------------------------
# GET /ingest — list this user's jobs
# ---------------------------------------------------------------------------


def _job_to_dict(j: Job, doc: Document | None) -> dict[str, Any]:
    return {
        "job_id": str(j.id),
        "document_id": str(j.document_id),
        "filename": doc.filename if doc else None,
        "status": j.status,
        "current_phase": j.current_phase,
        "pages_total": j.pages_total or 0,
        "pages_done": j.pages_done or 0,
        "error": j.error,
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "ended_at": j.ended_at.isoformat() if j.ended_at else None,
    }


@router.get("", summary="List the calling user's ingestion jobs")
async def list_jobs(user: AuthUser = Depends(current_user)) -> dict[str, Any]:
    Session = get_session_factory()
    async with Session() as sess:
        rows = (await sess.execute(
            select(Job, Document)
            .join(Document, Document.id == Job.document_id)
            .where(Job.user_id == user.id)
            .order_by(desc(Job.created_at))
            .limit(100)
        )).all()
    jobs = [_job_to_dict(j, d) for (j, d) in rows]
    return {"count": len(jobs), "jobs": jobs}


# ---------------------------------------------------------------------------
# GET /ingest/{job_id} — status / summary
# ---------------------------------------------------------------------------


@router.get("/{job_id}", summary="Job status + summary")
async def get_job_status(
    job_id: str, user: AuthUser = Depends(current_user)
) -> dict[str, Any]:
    Session = get_session_factory()
    async with Session() as sess:
        row = (await sess.execute(
            select(Job, Document)
            .join(Document, Document.id == Job.document_id)
            .where(Job.id == job_id)
        )).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    j, d = row
    if str(j.user_id) != user.id and user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Not authorized for this job")
    return _job_to_dict(j, d)
