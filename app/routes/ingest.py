"""Ingest routes — upload PDFs and query extraction results."""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from app.config import get_settings
from app.pipeline.orchestrator import PipelineOrchestrator, get_job
from app.utils.gemini_client import GeminiClient

router = APIRouter(prefix="/ingest", tags=["ingest"])


# ---------------------------------------------------------------------------
# Upload a PDF and run the current pipeline
# ---------------------------------------------------------------------------

@router.post("")
async def ingest(file: UploadFile):
    """Upload a PDF and run the ingestion pipeline.

    Currently executes:
    - Pre-Phase: PDF splitting (if >1000 pages / >50 MB)
    - Phase 0: Baseline text extraction (pymupdf4llm)
    - Phase 1: Document structure scan via Gemini (if API key configured)
    - Output: Per-page markdown + full document markdown to data/output/

    Returns a job summary with stats, timings, and per-page extraction results.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    # Sanitize filename to prevent path traversal
    safe_name = Path(PurePosixPath(file.filename).name).name
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Save upload to disk
    settings = get_settings()
    upload_path = settings.upload_dir / safe_name
    with open(upload_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Run pipeline
    gemini = GeminiClient(settings) if settings.gemini_api_key else None
    orchestrator = PipelineOrchestrator(settings, gemini_client=gemini)
    job = await orchestrator.run(upload_path, safe_name)

    # Build response with per-page preview
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
# Job status
# ---------------------------------------------------------------------------

@router.get("/{job_id}")
async def get_job_status(job_id: str):
    """Get the status and summary of a previous ingestion job."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job.to_summary()


# ---------------------------------------------------------------------------
# Page detail — full extraction for a specific page
# ---------------------------------------------------------------------------

@router.get("/{job_id}/pages/{page_num}")
async def get_page(job_id: str, page_num: int):
    """Get the full baseline extraction for a specific page.

    page_num is 0-indexed (matching internal representation).
    """
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    baseline = next((b for b in job.baselines if b.page_num == page_num), None)
    if not baseline:
        raise HTTPException(
            status_code=404,
            detail=f"Page {page_num} not found (valid range: 0-{job.total_pages - 1})",
        )

    return {
        "page_num": baseline.page_num,
        "char_count": baseline.char_count,
        "is_scanned": baseline.is_scanned,
        "image_count": baseline.image_count,
        "width": baseline.width,
        "height": baseline.height,
        "extraction_method": baseline.extraction_method,
        "text": baseline.text,
    }


# ---------------------------------------------------------------------------
# Page markdown — raw markdown output file
# ---------------------------------------------------------------------------

@router.get("/{job_id}/pages/{page_num}/markdown", response_class=PlainTextResponse)
async def get_page_markdown(job_id: str, page_num: int):
    """Get the markdown output file for a specific page."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if not job.output_dir:
        raise HTTPException(status_code=404, detail="No output generated yet")

    md_path = job.output_dir / f"page_{page_num:03d}.md"
    if not md_path.exists():
        raise HTTPException(status_code=404, detail=f"Markdown file not found for page {page_num}")

    return md_path.read_text(encoding="utf-8")
