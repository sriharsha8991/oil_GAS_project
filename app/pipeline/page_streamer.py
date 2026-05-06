"""Phase 1: Streaming page rasterizer + PageDeque (producer/consumer queue).

The rasterizer renders each PDF page to PNG bytes via PyMuPDF and pushes
the resulting `PageImage` to an `asyncio.Queue` the moment it's ready —
it does NOT wait for all pages to finish before pushing. Workers in
Phase 2 pop concurrently and start extraction immediately.

v4 additions:
- Adaptive DPI based on per-page text density (scanned/dense → high DPI,
  sparse → lower DPI). Cuts payload cost without quality loss.
- Optional PNG persistence to disk for downstream image-grounding (UI
  evidence cards crop bbox regions out of the persisted page PNG).
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import aiofiles
import fitz  # PyMuPDF

from app.models.schemas import PageBaseline, PageImage
from app.utils.logging import get_logger

logger = get_logger(__name__)


# Per-thread cache of the opened fitz.Document so each worker thread avoids
# re-parsing the PDF metadata on every page render (30-100ms savings per page).
# fitz.Document is not safe to share across threads, but one-per-thread is fine.
_thread_local = threading.local()


def _get_thread_doc(pdf_path: Path) -> "fitz.Document":
    doc = getattr(_thread_local, "doc", None)
    cached_path = getattr(_thread_local, "path", None)
    if doc is None or cached_path != str(pdf_path):
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
        doc = fitz.open(pdf_path)
        _thread_local.doc = doc
        _thread_local.path = str(pdf_path)
    return doc


# DPI tiers used by adaptive_dpi (override via Settings.render_dpi as the BASE).
_DPI_HIGH = 300   # scanned / dense pages
_DPI_BASE = 200   # default
_DPI_LOW = 150    # sparse / mostly-empty pages


def adaptive_dpi(baseline: PageBaseline | None, base_dpi: int) -> int:
    """Pick a render DPI based on page text density.

    - Scanned pages always render at 300 DPI (vision-only).
    - Pages with very high text density (small print, many tokens per area)
      render at 300 DPI.
    - Pages with very low density render at 150 DPI to save bytes.
    - Otherwise the configured base DPI is used.
    """
    if baseline is None:
        return base_dpi
    if baseline.is_scanned:
        return _DPI_HIGH

    area = (baseline.width or 1.0) * (baseline.height or 1.0)
    if area <= 1:
        return base_dpi

    # chars per square PDF point (1 pt = 1/72 inch). A4 ≈ 500_000 pt²
    density = baseline.char_count / area

    if density > 0.012:           # dense small print
        return _DPI_HIGH
    if density < 0.001 and baseline.char_count < 200:
        return _DPI_LOW
    return base_dpi


class PageDeque:
    """Async producer-consumer queue of `PageImage` objects.

    Producers call `push()` — blocks when the queue is full (backpressure).
    Consumers call `pop()` which returns `None` once the queue is closed
    AND empty (sentinel-based).

    The ``maxsize`` cap prevents the rasterizer from flooding memory when
    extraction workers are slower (Gemini latency >> render latency).
    Default 32 = ~6 MB of PNG bytes in-flight at 200 DPI.
    """

    def __init__(self, maxsize: int = 32) -> None:
        self._queue: asyncio.Queue[PageImage | None] = asyncio.Queue(maxsize=maxsize)
        self._closed: bool = False

    async def push(self, page: PageImage) -> None:
        await self._queue.put(page)

    async def pop(self) -> PageImage | None:
        # When the producer is done AND the queue has drained, return the
        # sentinel directly. This avoids needing one queued sentinel per
        # consumer (which can raise QueueFull when num_consumers > maxsize).
        while True:
            if self._closed and self._queue.empty():
                return None
            try:
                return await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

    def close(self, num_consumers: int = 0) -> None:
        """Mark the deque closed. Workers will exit once the queue drains.

        ``num_consumers`` is accepted for backwards compatibility but
        ignored — closure is now flag-based, not sentinel-based.
        """
        self._closed = True

    def qsize(self) -> int:
        return self._queue.qsize()


async def rasterize_pages(
    pdf_path: Path,
    baselines: list[PageBaseline],
    deque: PageDeque,
    *,
    dpi: int = 200,
    pages_dir: Path | None = None,
    use_adaptive_dpi: bool = True,
) -> int:
    """Render each PDF page to PNG and push to the deque (streaming).

    Args:
        pdf_path: Path to the source PDF.
        baselines: Phase 0 baselines, indexed by page_num.
        deque: Target async deque for downstream consumers.
        dpi: BASE render DPI (default 200). Adaptive logic may override.
        pages_dir: If provided, every rendered PNG is also persisted to
            ``{pages_dir}/page_{N:03d}.png`` for downstream image grounding.
        use_adaptive_dpi: If True, per-page DPI is chosen via :func:`adaptive_dpi`.

    Returns:
        Number of pages pushed.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    if pages_dir is not None:
        pages_dir.mkdir(parents=True, exist_ok=True)

    baseline_by_page: dict[int, PageBaseline] = {b.page_num: b for b in baselines}

    def _render(page_num: int, page_dpi: int) -> tuple[bytes, int, int]:
        """Render a single page in a thread (blocking PyMuPDF op).

        Reuses a per-thread fitz.Document instead of re-opening on every call.
        """
        doc = _get_thread_doc(pdf_path)
        page = doc[page_num]
        zoom = page_dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        png = pix.tobytes("png")
        return png, pix.width, pix.height

    # Get page count first (cheap)
    doc = fitz.open(pdf_path)
    page_count = len(doc)
    doc.close()

    # Bounded parallel render: launch all renders up front. The semaphore
    # caps in-flight threads to avoid RAM blowup on huge docs. We await the
    # tasks in page-number order so the deque.push order stays FIFO (the
    # downstream tests and workers rely on [0, 1, 2, ...] ordering).
    render_sem = asyncio.Semaphore(8)

    async def _render_one(pn: int) -> tuple[int, bytes, int, int, int, PageBaseline]:
        baseline_local = baseline_by_page.get(
            pn, PageBaseline(page_num=pn, text="", is_scanned=True)
        )
        page_dpi_local = adaptive_dpi(baseline_local, dpi) if use_adaptive_dpi else dpi
        async with render_sem:
            png, w, h = await asyncio.to_thread(_render, pn, page_dpi_local)
        return pn, png, w, h, page_dpi_local, baseline_local

    render_tasks = [
        asyncio.create_task(_render_one(pn)) for pn in range(page_count)
    ]
    persist_tasks: list[asyncio.Task] = []

    async def _persist_png(target: Path, data: bytes) -> None:
        async with aiofiles.open(target, "wb") as f:
            await f.write(data)

    pushed = 0
    for task in render_tasks:
        page_num, png_bytes, width, height, page_dpi, baseline = await task

        png_path: str | None = None
        if pages_dir is not None:
            target = pages_dir / f"page_{page_num:03d}.png"
            # Fire-and-forget: workers don't need the on-disk file, only
            # downstream post-processing does — we await all persists
            # before returning so the API can serve them immediately after.
            persist_tasks.append(
                asyncio.create_task(_persist_png(target, png_bytes))
            )
            png_path = str(target)

        page_image = PageImage(
            page_num=page_num,
            png_bytes=png_bytes,
            width=width,
            height=height,
            dpi=page_dpi,
            is_scanned=baseline.is_scanned,
            baseline_text=baseline.text,
            png_path=png_path,
        )

        await deque.push(page_image)
        pushed += 1

        logger.info(
            "page_rasterized",
            page_num=page_num,
            png_kb=len(png_bytes) // 1024,
            dpi=page_dpi,
            persisted=png_path is not None,
        )

    # Ensure every PNG is flushed to disk before we hand control back.
    if persist_tasks:
        await asyncio.gather(*persist_tasks)

    return pushed
