"""Celery task: ingest a PDF through the full pipeline.

Bridges async orchestrator with Celery's prefork pool by running the
pipeline inside a fresh asyncio event loop per task invocation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.workers.celery_app import celery_app


@celery_app.task(
    name="ingest_document",
    bind=True,
    queue="extract",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=2,
    default_retry_delay=30,
)
def ingest_document(self, pdf_path: str, filename: str, job_id: str) -> dict:
    """Run the full ingestion pipeline for a single PDF.

    Parameters
    ----------
    pdf_path : str
        Absolute path to the uploaded PDF inside the container.
    filename : str
        Original filename (used for output directory naming).
    job_id : str
        Pre-assigned job ID so the caller can poll for status.

    Returns
    -------
    dict
        Job summary dict (same shape as ``JobResult.to_summary()``).
    """
    from app.config import get_settings
    from app.pipeline.orchestrator import PipelineOrchestrator
    from app.utils.gemini_client import GeminiClient
    from app.vectorstore.qdrant_store import QdrantVectorStore

    settings = get_settings()
    gemini = GeminiClient(settings) if settings.gemini_api_key else None
    vector_store = QdrantVectorStore(settings)
    orchestrator = PipelineOrchestrator(
        settings, gemini_client=gemini, vector_store=vector_store
    )

    # Celery prefork workers don't have a running event loop —
    # create one for this task invocation.
    loop = asyncio.new_event_loop()
    try:
        job = loop.run_until_complete(
            orchestrator.run(Path(pdf_path), filename, job_id=job_id)
        )
    finally:
        loop.close()

    return job.to_summary()
