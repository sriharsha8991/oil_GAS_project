"""Pipeline orchestrator — manages the v3 streaming ingestion lifecycle.

Wires together all phases for a single PDF:
  Phase 0:  Baseline text extraction (pymupdf4llm) — local, no LLM
  Phase 1:  Streaming page rasterizer (PNG bytes → PageDeque)
  Phase 2:  100 concurrent per-page workers (LangGraph: extractor self-validates → escalator on flagged gaps)
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
from app.pipeline.postprocessor import PostProcessor, _build_page_map
from app.pipeline.preprocessing import Preprocessor
from app.storage.file_storage import FileStorage
from app.storage.redis_store import save_job
from app.utils.gemini_client import GeminiClient
from app.utils.logging import get_logger
from app.vectorstore.chunker import DocumentChunker
from app.vectorstore.embedder import GeminiEmbedder
from app.vectorstore.entity_indexer import build_entity_points, upsert_entity_points
from app.vectorstore.image_indexer import build_image_points, upsert_image_points
from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)


class JobResult:
    """Container for a v3 ingestion job."""

    def __init__(self, job_id: str, filename: str) -> None:
        self.job_id = job_id
        self.filename = filename
        self.doc_name: str | None = None
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
        # Wall-clock timing
        self.started_at: float = time.time()
        self.finished_at: float | None = None
        # Embedding token usage (aggregated by orchestrator)
        self.embedding_tokens: int = 0

    def to_summary(self) -> dict[str, Any]:
        scanned_count = sum(1 for b in self.baselines if b.is_scanned)
        avg_chars = (
            sum(b.char_count for b in self.baselines) / len(self.baselines)
            if self.baselines else 0
        )
        escalated = sum(1 for r in self.page_results.values() if r.get("escalated"))
        iter_counts: dict[int, int] = {1: 0, 2: 0}
        for r in self.page_results.values():
            it = r.get("iteration", 0)
            if it in iter_counts:
                iter_counts[it] += 1

        usage = self._aggregate_usage()
        cost = self._estimate_cost(usage)

        # Wall-clock duration: finished_at if job ended, else "now"
        end = self.finished_at if self.finished_at is not None else time.time()
        duration_s = round(end - self.started_at, 2)

        return {
            "job_id": self.job_id,
            "filename": self.filename,
            "doc_name": self.doc_name,
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
            "duration_seconds": duration_s,
            "stats": {
                "total_pages": self.total_pages,
                "scanned_pages": scanned_count,
                "text_pages": self.total_pages - scanned_count,
                "avg_chars_per_page": round(avg_chars),
            },
            "tokens": usage,
            "cost_usd": cost,
            "output_dir": str(self.output_dir) if self.output_dir else None,
        }

    def _aggregate_usage(self) -> dict[str, Any]:
        """Sum per-model token counts from every per-page trace entry."""
        per_model: dict[str, dict[str, int]] = {}
        for r in self.page_results.values():
            for entry in r.get("trace", []) or []:
                u = entry.get("usage")
                if not isinstance(u, dict):
                    continue
                model = u.get("model") or "unknown"
                slot = per_model.setdefault(
                    model,
                    {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0},
                )
                slot["input_tokens"] += int(u.get("input_tokens") or 0)
                slot["output_tokens"] += int(u.get("output_tokens") or 0)
                slot["total_tokens"] += int(u.get("total_tokens") or 0)
                slot["calls"] += 1

        totals = {
            "input_tokens": sum(m["input_tokens"] for m in per_model.values()),
            "output_tokens": sum(m["output_tokens"] for m in per_model.values()),
            "total_tokens": sum(m["total_tokens"] for m in per_model.values()),
            "calls": sum(m["calls"] for m in per_model.values()),
        }
        return {
            "by_model": per_model,
            "embedding_tokens": self.embedding_tokens,
            "totals": totals,
        }

    def _estimate_cost(self, usage: dict[str, Any]) -> dict[str, Any]:
        """Apply per-model pricing from settings to compute USD cost."""
        from app.config import get_settings  # local import: avoid cycles
        s = get_settings()

        breakdown: dict[str, float] = {}
        total = 0.0
        for model, slot in usage["by_model"].items():
            in_p, out_p = _pricing_for_model(model, s)
            cost = (
                slot["input_tokens"] * in_p / 1_000_000.0
                + slot["output_tokens"] * out_p / 1_000_000.0
            )
            breakdown[model] = round(cost, 6)
            total += cost

        embed_cost = self.embedding_tokens * s.embedding_usd_per_1m / 1_000_000.0
        breakdown["embedding"] = round(embed_cost, 6)
        total += embed_cost

        return {"by_model": breakdown, "total": round(total, 6), "currency": "USD"}

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


def _pricing_for_model(model: str, settings: Any) -> tuple[float, float]:
    """Return (input_price_per_1m, output_price_per_1m) USD for a model name."""
    m = (model or "").lower()
    if "pro" in m:
        return settings.pro_input_usd_per_1m, settings.pro_output_usd_per_1m
    # Default: Flash pricing (covers extractor + any unknown Flash variant).
    return settings.flash_input_usd_per_1m, settings.flash_output_usd_per_1m


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
        doc_name: str | None = None,
    ) -> JobResult:
        """Execute the pipeline end-to-end.

        ``doc_name`` controls the on-disk output directory and Qdrant
        ``doc_name`` payload field. When omitted it falls back to the
        filename stem (legacy behaviour); routes pass an explicit
        job-scoped name to avoid collisions when two PDFs share a name.
        """
        job_id = job_id or str(uuid.uuid4())
        doc_name = doc_name or Path(filename).stem
        # Evict eagerly on insertion so concurrent in-flight jobs don't
        # blow past the cap; the previous "evict only on completion"
        # behaviour allowed unbounded growth under burst load.
        _evict_old_jobs()
        job = JobResult(job_id=job_id, filename=filename)
        job.doc_name = doc_name
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
                        _worker(
                            deque,
                            graph,
                            job,
                            worker_id=i,
                            output_dir=self._storage.doc_dir(doc_name),
                            storage=self._storage,
                            doc_name=doc_name,
                        )
                    )
                    for i in range(num_workers)
                ]

                # Producer
                pages_dir = self._storage.pages_dir(doc_name)
                pushed = 0
                try:
                    pushed = await rasterize_pages(
                        pdf_path=pdf_path,
                        baselines=baselines,
                        deque=deque,
                        dpi=self._settings.render_dpi,
                        pages_dir=pages_dir,
                        use_adaptive_dpi=True,
                    )
                finally:
                    # ALWAYS signal completion to workers, even if the
                    # rasterizer raised. Without this, workers block forever
                    # on `deque.pop()` and the gather() below hangs.
                    deque.close(num_consumers=num_workers)

                # Wait for all workers to drain. return_exceptions=True so a
                # single page failure doesn't cancel the rest mid-flight —
                # workers already record per-page errors into job.page_results.
                worker_results = await asyncio.gather(
                    *worker_tasks, return_exceptions=True
                )
                for wr in worker_results:
                    if isinstance(wr, BaseException):
                        logger.error(
                            "worker_task_unhandled",
                            job_id=job_id,
                            error=str(wr),
                        )

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

                # Reuse postprocessor's flattening so we don't duplicate the
                # "final or extracted" rule across modules.
                page_map = _build_page_map(job.page_results)

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

                    # Approximate embedding tokens (~4 chars/token).
                    job.embedding_tokens += sum(len(c.text) for c in chunks) // 4

                    logger.info(
                        "pipeline_vectorize_chunks_done",
                        job_id=job_id,
                        chunks=len(chunks),
                        vectors_upserted=upserted,
                    )
                else:
                    logger.info(
                        "pipeline_vectorize_chunks_skipped",
                        job_id=job_id,
                        reason="no_chunks",
                    )

                # Phase 4b: Entity indexing
                entity_points = build_entity_points(
                    pp_result.resolved_by_page,
                    doc_name=doc_name,
                    job_id=job_id,
                    filename=filename,
                )
                entities_upserted = 0
                if entity_points:
                    entities_upserted = await upsert_entity_points(
                        entity_points, self._embedder, self._vector_store
                    )
                    job.embedding_tokens += sum(len(p.text) for p in entity_points) // 4

                # Phase 4c: Image description indexing
                image_points = build_image_points(
                    page_map,
                    doc_name=doc_name,
                    job_id=job_id,
                    filename=filename,
                )
                images_upserted = 0
                if image_points:
                    images_upserted = await upsert_image_points(
                        image_points, self._embedder, self._vector_store
                    )
                    job.embedding_tokens += sum(len(p.text) for p in image_points) // 4

                job.phase_timings["vectorizing"] = _ms_since(t0)
                logger.info(
                    "pipeline_vectorize_done",
                    job_id=job_id,
                    chunks_upserted=len(chunks) if chunks else 0,
                    entities_upserted=entities_upserted,
                    images_upserted=images_upserted,
                )

                job.persist()

            job.status = JobStatus.COMPLETED
            job.current_phase = "done"
            job.finished_at = time.time()
            job.persist()

            summary = job.to_summary()
            logger.info(
                "pipeline_complete",
                job_id=job_id,
                total_pages=job.total_pages,
                duration_seconds=summary["duration_seconds"],
                total_tokens=summary["tokens"]["totals"]["total_tokens"],
                cost_usd=summary["cost_usd"]["total"],
            )

        except Exception as exc:
            import traceback as _tb
            tb_str = _tb.format_exc()
            err_msg = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            job.status = JobStatus.FAILED
            job.error = err_msg
            job.finished_at = time.time()
            logger.error(
                "pipeline_failed",
                job_id=job_id,
                error=err_msg,
                traceback=tb_str,
            )
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
    storage: FileStorage | None = None,
    doc_name: str | None = None,
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

            # Lock final: if extractor self-passed and no escalation ran,
            # use whatever the last extraction produced.
            if not result.get("final"):
                result["final"] = result.get("extracted", {})

            # Strip raw bytes before persisting (memory + serialization).
            # Keep the on-disk PNG path so the API can serve the page image.
            result_to_store = {k: v for k, v in result.items() if k != "png_bytes"}
            result_to_store["png_path"] = page.png_path
            job.page_results[page.page_num] = result_to_store
            job.pages_processed = len(job.page_results)

            # Persist per-page state JSON so a future run can resume
            # without losing tables/entities/images.
            if storage is not None and doc_name is not None:
                try:
                    await storage.write_page_state(
                        doc_name, page.page_num, result_to_store
                    )
                except Exception:
                    logger.warning(
                        "worker_state_persist_failed",
                        page_num=page.page_num,
                        exc_info=True,
                    )

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
