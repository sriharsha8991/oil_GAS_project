"""Pipeline orchestrator — manages the v3 streaming ingestion lifecycle.

Wires together all phases for a single PDF:
  Phase 0:  Baseline text extraction (pymupdf4llm) — local, no LLM
  Phase 1:  Streaming page rasterizer (PNG bytes → PageDeque)
  Phase 2:  100 concurrent per-page workers (LangGraph: extractor → validator → escalator)
  Phase 3:  Post-processing (cross-page table merge, full document assembly)
  Phase 4:  Vectorize (chunk → embed → upsert to Qdrant)

Each ingestion job gets a unique ID; per-page results land in
`JobResult.page_results` as soon as each worker completes.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

from app.agents.graph import build_graph, page_image_to_state
from app.config import Settings
from app.models.schemas import JobStatus, PageBaseline
from app.models.state import PageState
from app.pipeline.page_streamer import PageDeque, rasterize_pages
from app.pipeline.postprocessor import PostProcessor
from app.pipeline.preprocessing import Preprocessor
from app.storage.file_storage import FileStorage
from app.storage.redis_store import save_job, update_job_field
from app.utils.gemini_client import GeminiClient
from app.utils.logging import get_logger
from app.vectorstore.chunker import DocumentChunker
from app.vectorstore.embedder import GeminiEmbedder
from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)


class JobResult:
    """Container for a v3 ingestion job."""

    def __init__(self, job_id: str, filename: str) -> None:
        self.job_id = job_id
        self.filename = filename
        self.status: JobStatus = JobStatus.PENDING
        self.total_pages: int = 0
        self.pages_processed: int = 0
        self.current_phase: str = ""
        self.error: str | None = None
        self.baselines: list[PageBaseline] = []
        # {page_num: PageState dict}
        self.page_results: dict[int, dict] = {}
        self.phase_timings: dict[str, int] = {}
        self.output_dir: Path | None = None

    def to_summary(self) -> dict[str, Any]:
        scanned_count = sum(1 for b in self.baselines if b.is_scanned)
        avg_chars = (
            sum(b.char_count for b in self.baselines) / len(self.baselines)
            if self.baselines else 0
        )
        escalated = sum(1 for r in self.page_results.values() if r.get("escalated"))
        iter_counts = {1: 0, 2: 0, 3: 0}
        for r in self.page_results.values():
            it = r.get("iteration", 0)
            if it in iter_counts:
                iter_counts[it] += 1

        return {
            "job_id": self.job_id,
            "filename": self.filename,
            "status": self.status.value,
            "total_pages": self.total_pages,
            "pages_processed": self.pages_processed,
            "current_phase": self.current_phase,
            "error": self.error,
            "extraction": {
                "pages_extracted": len(self.page_results),
                "escalated_to_pro": escalated,
                "iter_distribution": iter_counts,
            },
            "phase_timings_ms": self.phase_timings,
            "stats": {
                "total_pages": self.total_pages,
                "scanned_pages": scanned_count,
                "text_pages": self.total_pages - scanned_count,
                "avg_chars_per_page": round(avg_chars),
            },
            "output_dir": str(self.output_dir) if self.output_dir else None,
        }

    def persist(self) -> None:
        """Persist current job state to Redis (called at phase boundaries)."""
        try:
            save_job(self.job_id, self.to_summary())
        except Exception:
            logger.warning("redis_persist_failed", job_id=self.job_id, exc_info=True)


# In-memory job store — bounded LRU to prevent OOM on high-volume ingestion.
# Completed/failed jobs are persisted to Redis; only the most recent N are
# kept in memory for fast access by the sync endpoint and page-level queries.
_MAX_IN_MEMORY_JOBS = 20
_jobs: dict[str, JobResult] = {}


def _evict_old_jobs() -> None:
    """Evict oldest completed/failed jobs when the cache exceeds the cap."""
    if len(_jobs) <= _MAX_IN_MEMORY_JOBS:
        return
    evictable = [
        jid for jid, j in _jobs.items()
        if j.status in (JobStatus.COMPLETED, JobStatus.FAILED)
    ]
    # Remove oldest first (dict preserves insertion order in Python 3.7+)
    for jid in evictable[: len(_jobs) - _MAX_IN_MEMORY_JOBS]:
        del _jobs[jid]


def get_job(job_id: str) -> JobResult | None:
    return _jobs.get(job_id)


def list_jobs() -> list[JobResult]:
    """Return all jobs (most recent first by insertion order)."""
    return list(reversed(_jobs.values()))


class PipelineOrchestrator:
    """Runs the full v3 streaming ingestion pipeline for a single PDF."""

    def __init__(
        self,
        settings: Settings,
        gemini_client: GeminiClient | None = None,
        vector_store: QdrantVectorStore | None = None,
    ) -> None:
        self._settings = settings
        self._gemini = gemini_client
        self._preprocessor = Preprocessor(use_ocr=False)
        self._storage = FileStorage(settings.output_dir)
        self._vector_store = vector_store
        self._embedder = (
            GeminiEmbedder(settings) if settings.gemini_api_key else None
        )
        self._chunker = DocumentChunker(settings)
        # Compile the LangGraph once per orchestrator — the graph is a pure
        # function of (client, settings) and is safe to reuse across jobs.
        self._graph = (
            build_graph(client=self._gemini, settings=self._settings)
            if self._gemini is not None
            else None
        )

    async def run(
        self,
        pdf_path: Path,
        filename: str,
        *,
        job_id: str | None = None,
    ) -> JobResult:
        """Execute the pipeline end-to-end."""
        job_id = job_id or str(uuid.uuid4())
        job = JobResult(job_id=job_id, filename=filename)
        _jobs[job_id] = job

        try:
            job.status = JobStatus.PROCESSING
            job.persist()

            # ── Phase 0: Baseline Text ─────────────────────────────────
            job.current_phase = "preprocessing"
            t0 = time.perf_counter()
            baselines = await self._preprocessor.extract(pdf_path)
            job.baselines = baselines
            job.total_pages = len(baselines)
            job.phase_timings["preprocessing"] = _ms_since(t0)

            logger.info(
                "pipeline_preprocessing_done",
                job_id=job_id,
                pages=len(baselines),
                scanned=sum(1 for b in baselines if b.is_scanned),
            )
            job.persist()

            # ── Phases 1 + 2: Streaming Rasterizer + Worker Pool ───────
            if self._gemini:
                job.current_phase = "streaming_extraction"
                t0 = time.perf_counter()

                graph = self._graph
                deque = PageDeque()
                num_workers = self._settings.max_concurrent_pages

                # Start all workers BEFORE the rasterizer so they can pop
                # the very first page the moment it lands.
                worker_tasks = [
                    asyncio.create_task(
                        _worker(deque, graph, job, worker_id=i,
                                output_dir=self._storage.doc_dir(Path(filename).stem))
                    )
                    for i in range(num_workers)
                ]

                # Producer
                pages_dir = self._storage.pages_dir(Path(filename).stem)
                pushed = await rasterize_pages(
                    pdf_path=pdf_path,
                    baselines=baselines,
                    deque=deque,
                    dpi=self._settings.render_dpi,
                    pages_dir=pages_dir,
                    use_adaptive_dpi=True,
                )

                # Signal completion to workers
                deque.close(num_consumers=num_workers)

                # Wait for all workers to drain
                await asyncio.gather(*worker_tasks)

                job.phase_timings["streaming_extraction"] = _ms_since(t0)

                logger.info(
                    "pipeline_extraction_done",
                    job_id=job_id,
                    pages_pushed=pushed,
                    pages_extracted=len(job.page_results),
                    escalated=sum(
                        1 for r in job.page_results.values() if r.get("escalated")
                    ),
                )
                job.persist()

            # ── Phase 3: Post-Processing ───────────────────────────────
            job.current_phase = "postprocessing"
            t0 = time.perf_counter()

            doc_name = Path(filename).stem
            postprocessor = PostProcessor(self._storage)
            pp_result = await postprocessor.run(
                doc_name=doc_name,
                filename=filename,
                baselines=baselines,
                page_results=job.page_results,
            )
            job.output_dir = self._storage.doc_dir(doc_name)
            job.phase_timings["postprocessing"] = _ms_since(t0)

            logger.info(
                "pipeline_postprocessing_done",
                job_id=job_id,
                **pp_result.to_dict(),
            )

            # ── Phase 4: Vectorize (chunk → embed → Qdrant) ───────────
            if self._embedder and self._vector_store:
                job.current_phase = "vectorizing"
                t0 = time.perf_counter()

                # Build page_map the same way PostProcessor does
                page_map: dict[int, dict] = {}
                for pn, state in job.page_results.items():
                    extracted = state.get("final") or state.get("extracted") or {}
                    if extracted:
                        page_map[int(pn)] = extracted

                chunks = self._chunker.chunk_pages(
                    page_map,
                    doc_name=doc_name,
                    job_id=job_id,
                    filename=filename,
                )

                if chunks:
                    # Delete old vectors for this job (idempotent re-ingestion)
                    await self._vector_store.delete_by_job(job_id)

                    embeddings = await self._embedder.embed_texts(
                        [c.text for c in chunks]
                    )
                    upserted = await self._vector_store.upsert_chunks(
                        chunks, embeddings
                    )
                    job.phase_timings["vectorizing"] = _ms_since(t0)

                    logger.info(
                        "pipeline_vectorize_done",
                        job_id=job_id,
                        chunks=len(chunks),
                        vectors_upserted=upserted,
                    )
                else:
                    job.phase_timings["vectorizing"] = _ms_since(t0)
                    logger.info(
                        "pipeline_vectorize_skipped",
                        job_id=job_id,
                        reason="no_chunks",
                    )

                job.persist()

            job.status = JobStatus.COMPLETED
            job.current_phase = "done"
            job.persist()

            logger.info(
                "pipeline_complete",
                job_id=job_id,
                total_pages=job.total_pages,
                total_time_ms=sum(job.phase_timings.values()),
            )

        except Exception as exc:
            job.status = JobStatus.FAILED
            job.error = str(exc)
            logger.error("pipeline_failed", job_id=job_id, error=str(exc))
            job.persist()

        _evict_old_jobs()
        return job


async def _worker(
    deque: PageDeque,
    graph: Any,
    job: JobResult,
    *,
    worker_id: int,
    output_dir: Path | None = None,
) -> None:
    """Single worker: pop a PageImage, run the per-page graph, store result."""
    while True:
        page = await deque.pop()
        if page is None:
            return  # sentinel — clean exit

        try:
            # ── Idempotency check ──────────────────────────────────────
            # If page markdown already exists on disk (from a previous run
            # that was interrupted after extraction but before completion),
            # skip the expensive Gemini calls entirely.
            if output_dir is not None:
                marker = output_dir / f"page_{page.page_num:03d}.md"
                if marker.exists():
                    job.page_results[page.page_num] = {
                        "page_num": page.page_num,
                        "iteration": 0,
                        "escalated": False,
                        "png_path": page.png_path,
                        "final": {
                            "page_num": page.page_num,
                            "markdown": marker.read_text(encoding="utf-8"),
                            "tables": [],
                            "images": [],
                            "entities": [],
                            "cross_page_notes": "",
                        },
                        "skipped": True,
                    }
                    job.pages_processed = len(job.page_results)
                    logger.info(
                        "worker_page_skipped_idempotent",
                        worker_id=worker_id,
                        page_num=page.page_num,
                    )
                    continue

            initial_state: PageState = page_image_to_state(page)
            result = await graph.ainvoke(initial_state)

            # Release PNG bytes immediately — they've been consumed by
            # Gemini and are no longer needed.  On a 1000-page PDF this
            # frees ~200 MB that would otherwise stay pinned until the
            # worker task finishes.
            page.png_bytes = b""

            # Lock final: if validator never passed and no escalation ran,
            # use whatever the last extraction produced.
            if not result.get("final"):
                result["final"] = result.get("extracted", {})

            # Strip raw bytes before persisting (memory + serialization).
            # Keep the on-disk PNG path so the API can serve the page image.
            result_to_store = {k: v for k, v in result.items() if k != "png_bytes"}
            result_to_store["png_path"] = page.png_path
            job.page_results[page.page_num] = result_to_store
            job.pages_processed = len(job.page_results)

            logger.info(
                "worker_page_complete",
                worker_id=worker_id,
                page_num=page.page_num,
                iteration=result.get("iteration"),
                escalated=result.get("escalated", False),
            )
        except Exception as exc:
            logger.error(
                "worker_page_failed",
                worker_id=worker_id,
                page_num=page.page_num,
                error=str(exc),
            )
            job.page_results[page.page_num] = {
                "page_num": page.page_num,
                "iteration": 0,
                "escalated": False,
                "png_path": page.png_path,
                "final": {
                    "page_num": page.page_num,
                    "markdown": page.baseline_text,
                    "tables": [],
                    "images": [],
                    "entities": [],
                    "cross_page_notes": "",
                },
                "trace": [{"phase": "error", "error": str(exc)}],
            }


def _ms_since(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
