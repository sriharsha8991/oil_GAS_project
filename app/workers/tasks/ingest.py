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
def ingest_document(
    self,
    pdf_path: str,
    filename: str,
    job_id: str,
    doc_name: str | None = None,
) -> dict:
    """Run the full ingestion pipeline for a single PDF.

    Parameters
    ----------
    pdf_path : str
        Absolute path to the uploaded PDF inside the container.
    filename : str
        Original filename (used in API responses).
    job_id : str
        Pre-assigned job ID so the caller can poll for status.
    doc_name : str | None
        Optional job-scoped doc name for the on-disk output directory.
        When omitted the orchestrator falls back to the filename stem.
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
    # create one for this task invocation. set_event_loop is required
    # so libraries that call asyncio.get_event_loop() from sync helpers
    # (httpx, qdrant-client async) see the loop we're using.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # Make sure Qdrant collections exist in the worker process. The
        # FastAPI lifespan only ensures them in the API process; a fresh
        # Celery worker that has never seen the collections would 404.
        if vector_store is not None:
            try:
                loop.run_until_complete(vector_store.ensure_collection())
            except Exception:
                # Best effort — orchestrator still runs; failures will
                # surface there with a clearer message.
                pass

        job = loop.run_until_complete(
            orchestrator.run(
                Path(pdf_path), filename, job_id=job_id, doc_name=doc_name
            )
        )
    finally:
        # Cancel any tasks still pending (e.g. orphaned worker tasks left
        # over when an unhandled exception escaped orchestrator.run) and
        # wait for them to finish before closing async generators. This
        # avoids "aclose(): asynchronous generator is already running"
        # noise from LangGraph's Pregel.astream internals.
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except Exception:
            pass
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        asyncio.set_event_loop(None)
        loop.close()

    return job.to_summary()
