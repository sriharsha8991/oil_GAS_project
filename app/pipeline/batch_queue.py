"""Phase 2: Batch Queue — groups pages into section-aligned batches.

Takes the DocStructure from Phase 1 and the baselines from Phase 0
and produces a deque of PageBatch objects ready for Phase 3 processing.

Key rules:
  - Default batch size is ~5 pages.
  - Batches never split a cross-page table across boundaries.
  - Batches align to section boundaries when possible.
  - Each batch carries the section context, complexity tag, and
    recommended media resolution for the Phase 3 agents.
"""

from __future__ import annotations

import asyncio
from collections import deque

from app.models.schemas import (
    Complexity,
    CrossPageTable,
    DocStructure,
    PageBaseline,
    PageBatch,
    SectionInfo,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Default batch target size
DEFAULT_BATCH_SIZE = 5


# ---------------------------------------------------------------------------
# Async BatchQueue — consumed by Phase 3 workers
# ---------------------------------------------------------------------------


class BatchQueue:
    """Async-safe queue of PageBatch items for concurrent Phase 3 processing.

    Usage:
        queue = BatchQueue()
        queue.load(batches)          # fill from build_batches()
        while (batch := await queue.pop()) is not None:
            await process(batch)     # consumed by workers
    """

    def __init__(self) -> None:
        self._deque: deque[PageBatch] = deque()
        self._event = asyncio.Event()
        self._done = False

    @property
    def pending(self) -> int:
        """Number of batches still in the queue."""
        return len(self._deque)

    @property
    def is_done(self) -> bool:
        return self._done and len(self._deque) == 0

    def load(self, batches: list[PageBatch]) -> None:
        """Load a list of pre-built batches into the queue."""
        self._deque.extend(batches)
        if self._deque:
            self._event.set()
        logger.info("batch_queue_loaded", count=len(batches))

    async def push(self, batch: PageBatch) -> None:
        """Add a single batch (useful for dynamic re-queuing)."""
        self._deque.append(batch)
        self._event.set()

    async def pop(self) -> PageBatch | None:
        """Pop the next batch. Returns None when queue is done.

        Blocks until a batch is available or mark_done() is called.
        """
        while True:
            if self._deque:
                return self._deque.popleft()
            if self._done:
                return None
            self._event.clear()
            await self._event.wait()

    def mark_done(self) -> None:
        """Signal that no more batches will be added."""
        self._done = True
        self._event.set()


# ---------------------------------------------------------------------------
# Batch construction algorithm
# ---------------------------------------------------------------------------


def build_batches(
    doc_structure: DocStructure,
    baselines: list[PageBaseline],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[PageBatch]:
    """Build section-aligned batches from the DocStructure and baselines.

    Algorithm:
    1. Walk sections in order.
    2. For each section, split its page range into batches of ~batch_size.
    3. Never split a cross_page_table across batch boundaries.
    4. Tag each batch with section context, complexity, and media resolution.

    Args:
        doc_structure: Phase 1 output with sections, cross-page tables, etc.
        baselines: Phase 0 output — per-page baseline text.
        batch_size: Target pages per batch (default 5).

    Returns:
        List of PageBatch objects, ordered by page number.
    """
    if not baselines:
        return []

    total_pages = max(b.page_num for b in baselines) + 1
    baseline_map = {b.page_num: b for b in baselines}

    # Build a set of pages that must not be split from the *next* page
    # (i.e., "sticky" pages that bind to the following page).
    no_split_after = _build_no_split_set(doc_structure.cross_page_tables)

    # Walk sections and create batches
    batches: list[PageBatch] = []
    batch_id = 0
    covered_pages: set[int] = set()

    for section in doc_structure.sections:
        section_start = section.page_start
        section_end = section.page_end

        # Split this section's page range into batches
        page = section_start
        while page <= section_end:
            # Determine batch end
            batch_end = min(page + batch_size - 1, section_end)

            # Extend if we'd split a cross-page table
            batch_end = _extend_for_tables(batch_end, section_end, no_split_after)

            # Collect baselines for this batch
            batch_baselines = [
                baseline_map[p]
                for p in range(page, batch_end + 1)
                if p in baseline_map
            ]

            # Determine media resolution from complexity
            resolution = _complexity_to_resolution(section.complexity)

            batches.append(
                PageBatch(
                    batch_id=batch_id,
                    page_start=page,
                    page_end=batch_end,
                    section_context=section.name,
                    complexity=section.complexity,
                    baseline_texts=batch_baselines,
                    media_resolution=resolution,
                )
            )
            covered_pages.update(range(page, batch_end + 1))
            batch_id += 1
            page = batch_end + 1

    # Safety net: pick up any pages not covered by sections
    uncovered = sorted(set(range(total_pages)) - covered_pages)
    if uncovered:
        for chunk_start, chunk_end in _contiguous_ranges(uncovered):
            page = chunk_start
            while page <= chunk_end:
                batch_end = min(page + batch_size - 1, chunk_end)
                batch_end = _extend_for_tables(batch_end, chunk_end, no_split_after)
                batch_baselines = [
                    baseline_map[p]
                    for p in range(page, batch_end + 1)
                    if p in baseline_map
                ]
                batches.append(
                    PageBatch(
                        batch_id=batch_id,
                        page_start=page,
                        page_end=batch_end,
                        section_context="Uncovered",
                        complexity=Complexity.MEDIUM,
                        baseline_texts=batch_baselines,
                        media_resolution="MEDIUM",
                    )
                )
                batch_id += 1
                page = batch_end + 1

    # Sort by page_start to ensure ordering
    batches.sort(key=lambda b: b.page_start)

    # Re-number batch IDs sequentially after sorting
    for i, batch in enumerate(batches):
        batch.batch_id = i

    logger.info(
        "batches_built",
        total_batches=len(batches),
        total_pages=total_pages,
        batch_size=batch_size,
    )

    return batches


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_no_split_set(cross_page_tables: list[CrossPageTable]) -> set[int]:
    """Build a set of page numbers where we must NOT break a batch.

    If page N is in this set, then page N and N+1 must be in the same batch
    (because a cross-page table spans across that boundary).
    """
    no_split: set[int] = set()
    for table in cross_page_tables:
        for p in range(table.page_start, table.page_end):
            no_split.add(p)
    return no_split


def _extend_for_tables(
    batch_end: int,
    section_end: int,
    no_split_after: set[int],
) -> int:
    """Extend batch_end forward if it would split a cross-page table."""
    while batch_end in no_split_after and batch_end < section_end:
        batch_end += 1
    return batch_end


def _complexity_to_resolution(complexity: Complexity) -> str:
    """Map section complexity to Gemini media resolution."""
    return {
        Complexity.LOW: "MEDIUM",
        Complexity.MEDIUM: "MEDIUM",
        Complexity.HIGH: "HIGH",
    }.get(complexity, "MEDIUM")


def _contiguous_ranges(pages: list[int]) -> list[tuple[int, int]]:
    """Group a sorted list of page numbers into contiguous (start, end) ranges."""
    if not pages:
        return []
    ranges: list[tuple[int, int]] = []
    start = pages[0]
    prev = pages[0]
    for p in pages[1:]:
        if p == prev + 1:
            prev = p
        else:
            ranges.append((start, prev))
            start = p
            prev = p
    ranges.append((start, prev))
    return ranges
