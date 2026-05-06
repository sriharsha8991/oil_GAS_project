"""Ingest routes — public surface for document ingestion.

The public surface is intentionally minimal. Anything not listed here is
either an internal Python call (used by the RAG agent via ``app.agents``)
or a development helper that should not be exposed to end users.

Public endpoints
----------------
- ``POST   /ingest``                                 JSON: ingest by file path
- ``GET    /ingest``                                 list jobs
- ``GET    /ingest/{job_id}``                        job status / summary
- ``GET    /ingest/{job_id}/full``                   combined document markdown
- ``GET    /ingest/{job_id}/pages/{n}/image``        rendered page PNG
- ``GET    /ingest/{job_id}/pages/{n}/image/crop``   bbox crop for evidence

Anything else (per-page state, raw tables, raw entities, validator trace)
lives behind direct module imports for the chat agent. Those are NOT
exposed over HTTP.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF — for bbox cropping
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.models.schemas import JobStatus
from app.storage import redis_store
from app.utils.logging import get_logger

# Maximum number of files allowed in a single ingest request
_MAX_BATCH_FILES = 20

logger = get_logger(__name__)
router = APIRouter(prefix="/ingest", tags=["ingest"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _job_scoped_doc_name(safe_stem: str, job_id: str) -> str:
    """Build a job-scoped doc_name so concurrent ingestions of the same file
    don't collide on disk.
    """
    slug = job_id.replace("-", "")[:8]
    return f"{safe_stem}_{slug}"


def _resolve_pdf_path(raw_path: str, settings: Settings) -> Path:
    """Validate a server-side PDF path and return the resolved absolute path.

    Accepts any path the API process can read on the host filesystem. We
    intentionally do NOT confine to ``settings.upload_dir`` so operators
    can ingest from arbitrary mount points (network shares, /data, etc.).
    Validation:
      - must exist and be a regular file
      - must end in ``.pdf`` (case-insensitive)
      - must be within ``settings.max_upload_mb``
    """
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        # Resolve relative paths against the current working directory of
        # the API process — operators typically run with a known CWD.
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


def _resolve_doc_dir(job_id: str, settings: Settings) -> Path:
    """Look up the on-disk output directory for a job from Redis.

    Disk-backed endpoints must work across uvicorn workers and the Celery
    worker pool. Reading ``output_dir`` from the Redis manifest avoids
    depending on the in-memory ``_jobs`` cache, which is per-process.
    """
    manifest = redis_store.get_job(job_id)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    output_dir = manifest.get("output_dir")
    if not output_dir:
        raise HTTPException(status_code=404, detail="No output generated yet")
    path = Path(output_dir)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Output directory missing on disk")
    try:
        path.resolve().relative_to(settings.output_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid output path")
    return path


# ---------------------------------------------------------------------------
# POST /ingest — JSON: ingest by server-side file path
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    """JSON body for ingestion by file path."""

    file_paths: list[str] = Field(
        ...,
        min_length=1,
        max_length=_MAX_BATCH_FILES,
        description=(
            "Absolute or relative server-side paths to PDF files. "
            "Relative paths resolve against the API process working directory. "
            "Any path readable by the server is accepted."
        ),
        json_schema_extra={
            "examples": [
                [
                    "/data/well_reports/Sajaa_9_EOWR.pdf",
                    "./data/uploads/GSWA_Harvey_1_WCR.pdf",
                ]
            ]
        },
    )


@router.post(
    "",
    summary="Ingest one or more PDFs by server-side file path (async)",
)
async def ingest(req: IngestRequest) -> dict[str, Any]:
    """Submit up to 20 PDF paths. Each is dispatched to a Celery worker
    and returns immediately with a ``job_id``. Poll
    ``GET /ingest/{job_id}`` for status.
    """
    settings = get_settings()
    from app.workers.tasks.ingest import ingest_document

    dispatched: list[dict[str, str]] = []
    for raw_path in req.file_paths:
        pdf_path = _resolve_pdf_path(raw_path, settings)
        filename = pdf_path.name
        safe_stem = Path(filename).stem

        job_id = str(uuid.uuid4())
        doc_name = _job_scoped_doc_name(safe_stem, job_id)

        ingest_document.apply_async(
            args=[str(pdf_path), filename, job_id, doc_name],
            task_id=job_id,
        )
        dispatched.append({
            "job_id": job_id,
            "filename": filename,
            "source_path": str(pdf_path),
            "status": JobStatus.PENDING.value,
        })
        logger.info(
            "ingest_dispatched",
            job_id=job_id,
            source_path=str(pdf_path),
            doc_name=doc_name,
        )

    return {"total": len(dispatched), "jobs": dispatched}


# ---------------------------------------------------------------------------
# POST /ingest/upload — multipart: ingest by uploaded file (for the UI)
# ---------------------------------------------------------------------------


@router.post(
    "/upload",
    summary="Upload one or more PDFs and ingest them (multipart, for the UI)",
)
async def ingest_upload(
    files: list[UploadFile] = File(..., description="PDF files to ingest"),
) -> dict[str, Any]:
    """Accept multipart PDF uploads, persist them to ``settings.upload_dir``,
    then dispatch each to a Celery worker. Returns the same shape as
    ``POST /ingest``.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    if len(files) > _MAX_BATCH_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"At most {_MAX_BATCH_FILES} files per request",
        )

    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = settings.max_upload_mb * 1024 * 1024

    from app.workers.tasks.ingest import ingest_document

    dispatched: list[dict[str, str]] = []
    for f in files:
        original = f.filename or "upload.pdf"
        if not original.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=400,
                detail=f"Only PDF files are accepted: {original}",
            )

        job_id = str(uuid.uuid4())
        safe_stem = Path(original).stem
        # Disambiguate on disk so two uploads with the same name don't clash.
        target = settings.upload_dir / f"{safe_stem}_{job_id[:8]}.pdf"

        # Stream to disk while enforcing the size cap.
        size = 0
        with target.open("wb") as out:
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    out.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"File exceeds {settings.max_upload_mb} MB limit: "
                            f"{original}"
                        ),
                    )
                out.write(chunk)
        await f.close()

        doc_name = _job_scoped_doc_name(safe_stem, job_id)
        ingest_document.apply_async(
            args=[str(target), original, job_id, doc_name],
            task_id=job_id,
        )
        dispatched.append({
            "job_id": job_id,
            "filename": original,
            "source_path": str(target),
            "size_bytes": str(size),
            "status": JobStatus.PENDING.value,
        })
        logger.info(
            "ingest_uploaded",
            job_id=job_id,
            filename=original,
            size_bytes=size,
            doc_name=doc_name,
        )

    return {"total": len(dispatched), "jobs": dispatched}


# ---------------------------------------------------------------------------
# GET /ingest — list jobs
# ---------------------------------------------------------------------------


@router.get("", summary="List all ingestion jobs")
async def list_all_jobs() -> dict[str, Any]:
    """List the most recent ingestion jobs, sourced from Redis."""
    jobs = redis_store.list_jobs(limit=100)
    return {
        "count": len(jobs),
        "jobs": [
            {
                "job_id": j.get("job_id"),
                "filename": j.get("filename"),
                "status": j.get("status"),
                "total_pages": j.get("total_pages", 0),
                "pages_processed": j.get("pages_processed", 0),
                "current_phase": j.get("current_phase"),
            }
            for j in jobs
        ],
    }


# ---------------------------------------------------------------------------
# GET /ingest/{job_id} — status / summary
# ---------------------------------------------------------------------------


@router.get("/{job_id}", summary="Job status + summary")
async def get_job_status(job_id: str) -> dict[str, Any]:
    """Return the current job manifest from Redis, or fall back to Celery."""
    redis_job = redis_store.get_job(job_id)
    if redis_job:
        return redis_job

    from celery.result import AsyncResult

    from app.workers.celery_app import celery_app

    result = AsyncResult(job_id, app=celery_app)

    if result.state == "PENDING":
        return {"job_id": job_id, "status": "pending", "detail": "Task queued or unknown"}
    if result.state == "STARTED":
        return {"job_id": job_id, "status": "processing"}
    if result.state == "SUCCESS":
        return result.result  # the dict from job.to_summary()
    if result.state == "FAILURE":
        return {
            "job_id": job_id,
            "status": "failed",
            "error": str(result.result),
        }
    return {"job_id": job_id, "status": result.state.lower()}


# ---------------------------------------------------------------------------
# GET /ingest/{job_id}/full — full document markdown
# ---------------------------------------------------------------------------


@router.get(
    "/{job_id}/full",
    response_class=PlainTextResponse,
    summary="Combined full-document markdown",
)
async def get_full_document(job_id: str) -> str:
    settings = get_settings()
    doc_dir = _resolve_doc_dir(job_id, settings)
    full_md = doc_dir / "full_document.md"
    if not full_md.exists():
        raise HTTPException(status_code=404, detail="full_document.md not found")
    return full_md.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# GET /ingest/{job_id}/pages/{n}/image — rendered page PNG
# ---------------------------------------------------------------------------


@router.get(
    "/{job_id}/pages/{page_num}/image",
    summary="Rendered page PNG (for evidence grounding)",
)
async def get_page_image(job_id: str, page_num: int) -> FileResponse:
    settings = get_settings()
    doc_dir = _resolve_doc_dir(job_id, settings)
    png_path = doc_dir / "pages_png" / f"page_{page_num:03d}.png"
    if not png_path.exists():
        raise HTTPException(status_code=404, detail="Page PNG not persisted")
    return FileResponse(png_path, media_type="image/png")


# ---------------------------------------------------------------------------
# GET /ingest/{job_id}/pages/{n}/image/crop — bbox crop for evidence cards
# ---------------------------------------------------------------------------


@router.get(
    "/{job_id}/pages/{page_num}/image/crop",
    summary="Crop a bbox region from the rendered page PNG",
)
async def get_page_crop(
    job_id: str,
    page_num: int,
    ymin: int = Query(..., ge=0, le=1000),
    xmin: int = Query(..., ge=0, le=1000),
    ymax: int = Query(..., ge=0, le=1000),
    xmax: int = Query(..., ge=0, le=1000),
) -> Response:
    """Crop a region from the page PNG using Gemini's normalized 0-1000 bbox.

    Bbox order matches Gemini convention: ``[ymin, xmin, ymax, xmax]``.
    """
    if ymax <= ymin or xmax <= xmin:
        raise HTTPException(
            status_code=400, detail="Invalid bbox: ymax>ymin and xmax>xmin required"
        )

    settings = get_settings()
    doc_dir = _resolve_doc_dir(job_id, settings)
    png_path = doc_dir / "pages_png" / f"page_{page_num:03d}.png"
    if not png_path.exists():
        raise HTTPException(status_code=404, detail="Page PNG not persisted")

    def _crop() -> bytes:
        pix = fitz.Pixmap(str(png_path))
        w, h = pix.width, pix.height
        x0 = int(xmin / 1000 * w)
        y0 = int(ymin / 1000 * h)
        x1 = int(xmax / 1000 * w)
        y1 = int(ymax / 1000 * h)
        clip = fitz.IRect(x0, y0, x1, y1)
        cropped = fitz.Pixmap(pix, clip)
        return cropped.tobytes("png")

    png = await asyncio.to_thread(_crop)
    return Response(content=png, media_type="image/png")

