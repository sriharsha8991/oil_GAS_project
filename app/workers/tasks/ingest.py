"""Celery task: ingest a PDF through the full pipeline.

Bridges async orchestrator with Celery's prefork pool by running the
pipeline inside a fresh asyncio event loop per task invocation.
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.workers.celery_app import celery_app


async def _update_job_row(job_id: str, **fields: Any) -> None:
    """Update the jobs Postgres row in a fresh async session."""
    from sqlalchemy import update

    from app.db.engine import get_session_factory
    from app.db.models import Job

    Session = get_session_factory()
    async with Session() as sess:
        await sess.execute(
            update(Job).where(Job.id == job_id).values(**fields)
        )
        await sess.commit()


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
    user_id: str,
    project_id: str,
    document_id: str,
    job_id: str,
    filename: str,
    role: str,
    organization: str | None = None,
    doc_name: str | None = None,
) -> dict:
    """Run the full ingestion pipeline for a single PDF.

    The PDF must already live in the ``pdfs`` bucket at
    ``<user_id>/<project_id>/<document_id>/source.pdf`` (uploaded by the
    route handler before the task is dispatched).
    """
    from app.config import get_settings
    from app.pipeline.orchestrator import PipelineOrchestrator
    from app.storage.supabase_storage import SupabaseStorage
    from app.utils.gemini_client import GeminiClient
    from app.vectorstore.qdrant_store import QdrantVectorStore

    settings = get_settings()
    gemini = GeminiClient(settings) if settings.gemini_api_key else None
    vector_store = QdrantVectorStore(settings)
    storage = SupabaseStorage(settings)
    orchestrator = PipelineOrchestrator(
        settings,
        gemini_client=gemini,
        vector_store=vector_store,
        storage=storage,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    tmp_pdf: Path | None = None
    try:
        loop.run_until_complete(_update_job_row(
            job_id,
            status="running",
            current_phase="starting",
            started_at=datetime.now(timezone.utc),
        ))

        if vector_store is not None:
            try:
                loop.run_until_complete(vector_store.ensure_collection())
            except Exception:
                pass

        pdf_bytes = loop.run_until_complete(
            storage.read_pdf(user_id, project_id, document_id)
        )
        if pdf_bytes is None:
            raise RuntimeError(
                f"Source PDF missing in pdfs bucket for "
                f"{user_id}/{project_id}/{document_id}"
            )
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            tmp_pdf = Path(f.name)

        job = loop.run_until_complete(
            orchestrator.run(
                tmp_pdf,
                filename,
                user_id=user_id,
                role=role,
                organization=organization,
                project_id=project_id,
                document_id=document_id,
                job_id=job_id,
                doc_name=doc_name,
            )
        )

        summary = job.to_summary()
        loop.run_until_complete(_update_job_row(
            job_id,
            status="succeeded" if job.status.value == "completed" else "failed",
            current_phase=job.current_phase,
            pages_total=job.total_pages,
            pages_done=job.pages_processed,
            error=job.error,
            ended_at=datetime.now(timezone.utc),
        ))
        return summary
    except Exception as exc:
        try:
            loop.run_until_complete(_update_job_row(
                job_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                ended_at=datetime.now(timezone.utc),
            ))
        except Exception:
            pass
        raise
    finally:
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
        # Dispose the SQLAlchemy async engine before the loop closes — its
        # asyncpg connections are bound to this loop, and a stale pool would
        # raise "Event loop is closed" on the next task that runs in a fresh
        # loop.
        try:
            from app.db.engine import dispose_engine

            loop.run_until_complete(dispose_engine())
        except Exception:
            pass
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        asyncio.set_event_loop(None)
        loop.close()
        if tmp_pdf is not None:
            try:
                tmp_pdf.unlink(missing_ok=True)
            except Exception:
                pass
