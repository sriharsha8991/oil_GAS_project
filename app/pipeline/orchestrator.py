"""Pipeline orchestrator — manages the full ingestion lifecycle for a document.

Wires together all phases:
  Pre-Phase: PDF splitting (if >1000 pages)
  Phase 0:   Baseline text extraction (pymupdf4llm)
  Phase 1:   Document structure scan via Gemini (TOC, sections, entities)
  Phase 2:   Batch queue construction (section-aligned page batches)
  Phase 3+:  (Future — LangGraph agent extraction, validation)

Each ingestion job gets a unique ID and tracks progress through phases.
Results are written to disk and returned via the API.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from app.config import Settings
from app.models.schemas import DocStructure, JobStatus, PageBaseline, PageBatch
from app.pipeline.batch_queue import BatchQueue, build_batches
from app.pipeline.preprocessing import Preprocessor
from app.pipeline.structure_scan import ScanResult, StructureScanner
from app.storage.file_storage import FileStorage
from app.utils.gemini_client import GeminiClient
from app.utils.logging import get_logger
from app.utils.pdf_splitter import PDFSplitter, SplitResult

logger = get_logger(__name__)


class JobResult:
    """Container for a completed ingestion job."""

    def __init__(self, job_id: str, filename: str) -> None:
        self.job_id = job_id
        self.filename = filename
        self.status: JobStatus = JobStatus.PENDING
        self.total_pages: int = 0
        self.pages_processed: int = 0
        self.current_phase: str = ""
        self.error: str | None = None
        self.split_result: SplitResult | None = None
        self.baselines: list[PageBaseline] = []
        self.doc_structure: DocStructure | None = None
        self.scan_result: ScanResult | None = None
        self.batches: list[PageBatch] = []
        self.batch_queue: BatchQueue | None = None
        self.phase_timings: dict[str, int] = {}  # phase → ms
        self.output_dir: Path | None = None

    def to_summary(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly summary dict."""
        scanned_count = sum(1 for b in self.baselines if b.is_scanned)
        avg_chars = (
            sum(b.char_count for b in self.baselines) / len(self.baselines)
            if self.baselines
            else 0
        )
        return {
            "job_id": self.job_id,
            "filename": self.filename,
            "status": self.status.value,
            "total_pages": self.total_pages,
            "pages_processed": self.pages_processed,
            "current_phase": self.current_phase,
            "error": self.error,
            "split": {
                "was_split": self.split_result.was_split if self.split_result else False,
                "num_chunks": len(self.split_result.chunks) if self.split_result else 1,
                "chunk_pages": (
                    [c.page_count for c in self.split_result.chunks]
                    if self.split_result
                    else []
                ),
            },
            "doc_structure": (
                self.doc_structure.model_dump() if self.doc_structure else None
            ),
            "batches": {
                "total": len(self.batches),
                "batch_sizes": [
                    b.page_end - b.page_start + 1 for b in self.batches
                ],
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


# In-memory job store (replace with DB/Redis in production)
_jobs: dict[str, JobResult] = {}


def get_job(job_id: str) -> JobResult | None:
    return _jobs.get(job_id)


class PipelineOrchestrator:
    """Runs the full ingestion pipeline for a single PDF."""

    def __init__(
        self,
        settings: Settings,
        gemini_client: GeminiClient | None = None,
    ) -> None:
        self._settings = settings
        self._gemini = gemini_client
        self._splitter = PDFSplitter(
            max_pages=settings.pdf_max_pages,
            max_size_mb=settings.pdf_max_size_mb,
            target_pages=settings.pdf_split_target_pages,
            min_pages=settings.pdf_split_min_pages,
        )
        self._preprocessor = Preprocessor(use_ocr=False)
        self._scanner = StructureScanner(gemini_client, settings) if gemini_client else None
        self._storage = FileStorage(settings.output_dir)

    async def run(self, pdf_path: Path, filename: str) -> JobResult:
        """Execute the pipeline end-to-end.

        Args:
            pdf_path: Path to the uploaded PDF on disk.
            filename: Original filename from the upload.

        Returns:
            JobResult with all extraction results and metadata.
        """
        job_id = str(uuid.uuid4())
        job = JobResult(job_id=job_id, filename=filename)
        _jobs[job_id] = job

        try:
            job.status = JobStatus.PROCESSING

            # ── Pre-Phase: PDF Splitting ─────────────────────────────
            job.current_phase = "splitting"
            t0 = time.perf_counter()

            split_result = self._splitter.split(pdf_path, self._settings.output_dir / "_chunks")
            job.split_result = split_result
            job.total_pages = split_result.total_pages
            job.phase_timings["splitting"] = _ms_since(t0)

            logger.info(
                "pipeline_split_done",
                job_id=job_id,
                total_pages=split_result.total_pages,
                was_split=split_result.was_split,
                num_chunks=len(split_result.chunks),
            )

            # ── Phase 0: Baseline Text Extraction ────────────────────
            job.current_phase = "preprocessing"
            t0 = time.perf_counter()

            all_baselines: list[PageBaseline] = []
            for chunk in split_result.chunks:
                chunk_baselines = await self._preprocessor.extract(chunk.file_path)

                # Remap page numbers to global (original PDF) page numbers
                for b in chunk_baselines:
                    b.page_num = b.page_num + chunk.page_start

                all_baselines.extend(chunk_baselines)
                job.pages_processed = len(all_baselines)

            job.baselines = all_baselines
            job.phase_timings["preprocessing"] = _ms_since(t0)

            logger.info(
                "pipeline_preprocessing_done",
                job_id=job_id,
                pages=len(all_baselines),
                scanned=sum(1 for b in all_baselines if b.is_scanned),
            )

            # ── Phase 1: Document Structure Scan ─────────────────────
            if self._scanner:
                job.current_phase = "structure_scan"
                t0 = time.perf_counter()

                scan_result = await self._scanner.scan(
                    pdf_path=pdf_path,
                    doc_id=job_id,
                    total_pages=job.total_pages,
                )
                job.doc_structure = scan_result.doc_structure
                job.scan_result = scan_result
                job.phase_timings["structure_scan"] = _ms_since(t0)

                logger.info(
                    "pipeline_structure_scan_done",
                    job_id=job_id,
                    document_type=scan_result.doc_structure.document_type,
                    sections=len(scan_result.doc_structure.sections),
                    cross_page_tables=len(scan_result.doc_structure.cross_page_tables),
                )

            # ── Phase 2: Batch Queue Construction ─────────────────
            if job.doc_structure:
                job.current_phase = "batching"
                t0 = time.perf_counter()

                batches = build_batches(
                    doc_structure=job.doc_structure,
                    baselines=all_baselines,
                    batch_size=self._settings.batch_size,
                )
                job.batches = batches

                # Create async queue for Phase 3 workers
                queue = BatchQueue()
                queue.load(batches)
                queue.mark_done()
                job.batch_queue = queue

                job.phase_timings["batching"] = _ms_since(t0)

                logger.info(
                    "pipeline_batching_done",
                    job_id=job_id,
                    total_batches=len(batches),
                    batch_sizes=[b.page_end - b.page_start + 1 for b in batches],
                )

            # ── Write Output: Per-page markdown ──────────────────────
            job.current_phase = "writing_output"
            t0 = time.perf_counter()

            doc_name = Path(filename).stem
            for baseline in all_baselines:
                page_md = self._format_page_markdown(baseline)
                await self._storage.write_page_markdown(doc_name, baseline.page_num, page_md)

            # Write combined document
            full_md = self._format_full_document(filename, all_baselines, split_result)
            await self._storage.write_full_document(doc_name, full_md)
            job.output_dir = self._storage.doc_dir(doc_name)
            job.phase_timings["writing_output"] = _ms_since(t0)

            # ── Done ─────────────────────────────────────────────────
            job.status = JobStatus.COMPLETED
            job.current_phase = "done"

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

        return job

    def _format_page_markdown(self, baseline: PageBaseline) -> str:
        """Format a single page's baseline extraction as markdown."""
        lines = [
            f"# Page {baseline.page_num + 1}",
            "",
            f"**Extraction method**: {baseline.extraction_method}  ",
            f"**Characters**: {baseline.char_count}  ",
            f"**Scanned**: {baseline.is_scanned}  ",
            f"**Images on page**: {baseline.image_count}  ",
            f"**Dimensions**: {baseline.width:.0f} x {baseline.height:.0f}",
            "",
            "---",
            "",
        ]
        if baseline.text:
            lines.append(baseline.text)
        else:
            lines.append("*(No text extracted — page appears to be image-only)*")
        lines.append("")
        return "\n".join(lines)

    def _format_full_document(
        self,
        filename: str,
        baselines: list[PageBaseline],
        split_result: SplitResult,
    ) -> str:
        """Format the full document as a single merged markdown."""
        scanned = sum(1 for b in baselines if b.is_scanned)
        total_chars = sum(b.char_count for b in baselines)

        header = [
            f"# {filename}",
            "",
            f"**Total pages**: {len(baselines)}  ",
            f"**Text pages**: {len(baselines) - scanned}  ",
            f"**Scanned pages**: {scanned}  ",
            f"**Total characters**: {total_chars:,}  ",
            f"**Split into chunks**: {split_result.was_split} "
            f"({len(split_result.chunks)} chunk(s))",
            "",
            "---",
            "",
        ]

        pages = []
        for b in baselines:
            pages.append(f"## Page {b.page_num + 1}")
            pages.append("")
            if b.text:
                pages.append(b.text)
            else:
                pages.append("*(No text extracted)*")
            pages.append("")
            pages.append("---")
            pages.append("")

        return "\n".join(header + pages)


def _ms_since(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)
