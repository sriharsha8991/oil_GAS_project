"""Ingest routes — upload PDFs, query extraction results, serve grounded evidence."""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

import fitz  # PyMuPDF — for bbox cropping
from fastapi import APIRouter, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response

from app.config import get_settings
from app.models.schemas import JobStatus
from app.pipeline.orchestrator import (
    JobResult,
    PipelineOrchestrator,
    get_job,
    list_jobs,
)
from app.storage import redis_store
from app.utils.gemini_client import GeminiClient
from app.utils.logging import get_logger
from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)
router = APIRouter(prefix="/ingest", tags=["ingest"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_filename(name: str | None) -> str:
    if not name or not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")
    safe = Path(PurePosixPath(name).name).name
    if not safe:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return safe


def _require_job(job_id: str) -> JobResult:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


def _require_page(job: JobResult, page_num: int) -> dict:
    if not (0 <= page_num < job.total_pages):
        raise HTTPException(
            status_code=404,
            detail=f"Page {page_num} out of range (0-{job.total_pages - 1})",
        )
    state = job.page_results.get(page_num)
    if not state:
        raise HTTPException(
            status_code=404,
            detail=f"No extraction result yet for page {page_num}",
        )
    return state


def _final_extracted(state: dict) -> dict:
    return state.get("final") or state.get("extracted") or {}


# ---------------------------------------------------------------------------
# Upload + run pipeline (sync — blocks until complete)
# ---------------------------------------------------------------------------


@router.post("", summary="Upload PDF and run the pipeline synchronously")
async def ingest(file: UploadFile) -> dict[str, Any]:
    """Upload a PDF, run the full v3 streaming pipeline, return summary."""
    safe_name = _safe_filename(file.filename)

    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    upload_path = settings.upload_dir / safe_name
    with open(upload_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    gemini = GeminiClient(settings) if settings.gemini_api_key else None
    vector_store = QdrantVectorStore(settings)
    orchestrator = PipelineOrchestrator(settings, gemini_client=gemini, vector_store=vector_store)
    job = await orchestrator.run(upload_path, safe_name)

    summary = job.to_summary()
    summary["pages"] = [
        {
            "page_num": b.page_num,
            "char_count": b.char_count,
            "is_scanned": b.is_scanned,
            "image_count": b.image_count,
            "extraction_method": b.extraction_method,
            "text_preview": b.text[:500] + ("..." if len(b.text) > 500 else ""),
        }
        for b in job.baselines
    ]
    return summary


# ---------------------------------------------------------------------------
# Upload + run pipeline (async — returns job_id immediately)
# ---------------------------------------------------------------------------


@router.post("/async", summary="Upload PDF and run the pipeline in the background")
async def ingest_async(file: UploadFile) -> dict[str, str]:
    """Upload a PDF, dispatch ingestion to Celery, return job_id.

    Poll ``GET /ingest/{job_id}`` for status.
    """
    safe_name = _safe_filename(file.filename)
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    upload_path = settings.upload_dir / safe_name
    with open(upload_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    job_id = str(uuid.uuid4())

    from app.workers.tasks.ingest import ingest_document

    ingest_document.apply_async(
        args=[str(upload_path), safe_name, job_id],
        task_id=job_id,
    )

    return {"job_id": job_id, "status": JobStatus.PENDING.value, "filename": safe_name}


# ---------------------------------------------------------------------------
# Jobs list
# ---------------------------------------------------------------------------


@router.get("", summary="List all ingestion jobs")
async def list_all_jobs() -> dict[str, Any]:
    # Merge in-memory jobs (current process) with Redis-backed jobs
    in_memory = list_jobs()
    in_memory_ids = {j.job_id for j in in_memory}

    # In-memory jobs (richer data — have baselines, page_results)
    result_jobs = [
        {
            "job_id": j.job_id,
            "filename": j.filename,
            "status": j.status.value,
            "total_pages": j.total_pages,
            "pages_processed": j.pages_processed,
            "current_phase": j.current_phase,
        }
        for j in in_memory
    ]

    # Redis jobs not already in memory
    redis_jobs = redis_store.list_jobs(limit=100)
    for rj in redis_jobs:
        if rj.get("job_id") not in in_memory_ids:
            result_jobs.append({
                "job_id": rj.get("job_id"),
                "filename": rj.get("filename"),
                "status": rj.get("status"),
                "total_pages": rj.get("total_pages", 0),
                "pages_processed": rj.get("pages_processed", 0),
                "current_phase": rj.get("current_phase"),
            })

    return {"count": len(result_jobs), "jobs": result_jobs}


# ---------------------------------------------------------------------------
# Job status / summary
# ---------------------------------------------------------------------------


@router.get("/{job_id}", summary="Job status + summary")
async def get_job_status(job_id: str) -> dict[str, Any]:
    # 1. Check in-memory store (sync pipeline or same-process)
    job = get_job(job_id)
    if job:
        return job.to_summary()

    # 2. Check Redis manifest (durable, cross-container)
    redis_job = redis_store.get_job(job_id)
    if redis_job:
        return redis_job

    # 3. Fall back to Celery result backend (async pipeline via worker)
    from celery.result import AsyncResult

    from app.workers.celery_app import celery_app

    result = AsyncResult(job_id, app=celery_app)

    if result.state == "PENDING":
        return {"job_id": job_id, "status": "pending", "detail": "Task queued or unknown"}
    elif result.state == "STARTED":
        return {"job_id": job_id, "status": "processing"}
    elif result.state == "SUCCESS":
        return result.result  # the dict from job.to_summary()
    elif result.state == "FAILURE":
        return {
            "job_id": job_id,
            "status": "failed",
            "error": str(result.result),
        }
    else:
        return {"job_id": job_id, "status": result.state.lower()}


# ---------------------------------------------------------------------------
# Full document markdown
# ---------------------------------------------------------------------------


@router.get(
    "/{job_id}/full",
    response_class=PlainTextResponse,
    summary="Combined full-document markdown",
)
async def get_full_document(job_id: str) -> str:
    job = _require_job(job_id)
    if not job.output_dir:
        raise HTTPException(status_code=404, detail="No output generated yet")
    full_md = job.output_dir / "full_document.md"
    if not full_md.exists():
        raise HTTPException(status_code=404, detail="full_document.md not found")
    return full_md.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Per-page baseline + extraction
# ---------------------------------------------------------------------------


@router.get("/{job_id}/pages/{page_num}", summary="Page baseline + extraction summary")
async def get_page(job_id: str, page_num: int) -> dict[str, Any]:
    job = _require_job(job_id)
    baseline = next((b for b in job.baselines if b.page_num == page_num), None)
    if not baseline:
        raise HTTPException(
            status_code=404,
            detail=f"Page {page_num} not found (range 0-{job.total_pages - 1})",
        )

    state = job.page_results.get(page_num) or {}
    extracted = _final_extracted(state)
    md = extracted.get("markdown", "")

    return {
        "page_num": baseline.page_num,
        "char_count": baseline.char_count,
        "is_scanned": baseline.is_scanned,
        "image_count": baseline.image_count,
        "width": baseline.width,
        "height": baseline.height,
        "extraction_method": baseline.extraction_method,
        "baseline_text": baseline.text,
        "iteration": state.get("iteration"),
        "escalated": state.get("escalated"),
        "validation": state.get("validation"),
        "extracted": {
            "markdown_preview": md[:500] + ("..." if len(md) > 500 else ""),
            "tables": len(extracted.get("tables", [])),
            "images": len(extracted.get("images", [])),
            "entities": len(extracted.get("entities", [])),
        },
        "png_path": state.get("png_path"),
    }


@router.get(
    "/{job_id}/pages/{page_num}/markdown",
    response_class=PlainTextResponse,
    summary="Per-page extracted markdown file",
)
async def get_page_markdown(job_id: str, page_num: int) -> str:
    job = _require_job(job_id)
    if not job.output_dir:
        raise HTTPException(status_code=404, detail="No output generated yet")
    md_path = job.output_dir / f"page_{page_num:03d}.md"
    if not md_path.exists():
        raise HTTPException(status_code=404, detail=f"Markdown for page {page_num} not found")
    return md_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Image grounding — page PNG and on-demand bbox crop
# ---------------------------------------------------------------------------


@router.get(
    "/{job_id}/pages/{page_num}/image",
    summary="Rendered page PNG (for evidence grounding)",
)
async def get_page_image(job_id: str, page_num: int) -> FileResponse:
    job = _require_job(job_id)
    state = _require_page(job, page_num)
    png_path = state.get("png_path")
    if not png_path or not Path(png_path).exists():
        raise HTTPException(status_code=404, detail="Page PNG not persisted")
    return FileResponse(png_path, media_type="image/png")


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

    job = _require_job(job_id)
    state = _require_page(job, page_num)
    png_path = state.get("png_path")
    if not png_path or not Path(png_path).exists():
        raise HTTPException(status_code=404, detail="Page PNG not persisted")

    def _crop() -> bytes:
        pix = fitz.Pixmap(png_path)
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


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


@router.get("/{job_id}/pages/{page_num}/tables", summary="Tables on a single page")
async def get_page_tables(job_id: str, page_num: int) -> dict[str, Any]:
    job = _require_job(job_id)
    state = _require_page(job, page_num)
    tables = _final_extracted(state).get("tables", []) or []
    return {"page_num": page_num, "count": len(tables), "tables": tables}


@router.get(
    "/{job_id}/tables/{page_num}/{table_index}",
    summary="Single persisted table JSON file",
)
async def get_table_file(
    job_id: str, page_num: int, table_index: int
) -> dict[str, Any]:
    job = _require_job(job_id)
    if not job.output_dir:
        raise HTTPException(status_code=404, detail="No output generated yet")
    path = (
        job.output_dir
        / "tables"
        / f"page_{page_num:03d}_table_{table_index:02d}.json"
    )
    if not path.exists():
        raise HTTPException(status_code=404, detail="Table file not found")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


@router.get("/{job_id}/entities", summary="All entities extracted from the document")
async def get_entities(
    job_id: str,
    type: str | None = Query(None, description="Filter by entity type"),
) -> dict[str, Any]:
    job = _require_job(job_id)
    by_page: dict[int, list[dict]] = {}
    by_type: dict[str, int] = {}

    for pn, state in sorted(job.page_results.items()):
        items = _final_extracted(state).get("entities", []) or []
        if type:
            items = [e for e in items if e.get("type") == type]
        if items:
            by_page[pn] = items
            for e in items:
                t = e.get("type", "other") or "other"
                by_type[t] = by_type.get(t, 0) + 1

    return {
        "job_id": job_id,
        "filter_type": type,
        "count": sum(len(v) for v in by_page.values()),
        "by_type": by_type,
        "by_page": {str(k): v for k, v in by_page.items()},
    }


@router.get(
    "/{job_id}/pages/{page_num}/entities",
    summary="Entities for a single page",
)
async def get_page_entities(job_id: str, page_num: int) -> dict[str, Any]:
    job = _require_job(job_id)
    state = _require_page(job, page_num)
    items = _final_extracted(state).get("entities", []) or []
    return {"page_num": page_num, "count": len(items), "entities": items}


# ---------------------------------------------------------------------------
# Per-page extraction trace (debugging / observability)
# ---------------------------------------------------------------------------


@router.get(
    "/{job_id}/pages/{page_num}/trace",
    summary="Per-page extraction trace (extractor / validator / escalation timings)",
)
async def get_page_trace(job_id: str, page_num: int) -> dict[str, Any]:
    job = _require_job(job_id)
    state = _require_page(job, page_num)
    return {
        "page_num": page_num,
        "iteration": state.get("iteration"),
        "escalated": state.get("escalated"),
        "validation": state.get("validation"),
        "trace": state.get("trace", []),
    }
