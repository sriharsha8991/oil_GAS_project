"""PDF splitting utility for large documents that exceed Gemini API limits.

Gemini Files API hard limits (as of March 2026):
  - 50 MB per file upload
  - 1000 pages per PDF

Well reports can reach 6000+ pages, so we split them into sub-PDFs
before any Gemini interaction.

Splitting strategy:
  1. If PDF is within limits → return as-is (single chunk)
  2. Otherwise → split at section boundaries using PyMuPDF TOC
  3. If no TOC → split at fixed intervals (~800 pages)
  4. Never produce chunks smaller than min_pages (avoids tiny fragments)

Each chunk is a self-contained PDF on disk that can be independently
uploaded, cached, and processed by the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

from app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PDFChunk:
    """A contiguous range of pages written to a sub-PDF file."""
    chunk_index: int
    page_start: int          # 0-indexed inclusive
    page_end: int            # 0-indexed inclusive
    page_count: int
    file_path: Path
    file_size_mb: float
    toc_sections: list[str] = field(default_factory=list)  # Section names starting in this chunk

    @property
    def is_original(self) -> bool:
        """True if this chunk IS the original file (no split needed)."""
        return self.chunk_index == 0 and "_chunk_" not in self.file_path.stem


@dataclass
class SplitResult:
    """Result of splitting a PDF into chunks."""
    original_path: Path
    total_pages: int
    total_size_mb: float
    was_split: bool
    chunks: list[PDFChunk]


class PDFSplitter:
    """Split large PDFs into Gemini-compatible chunks.

    Usage:
        splitter = PDFSplitter(max_pages=1000, target_pages=800)
        result = await splitter.split(Path("6000_page_report.pdf"), output_dir)
        for chunk in result.chunks:
            # each chunk.file_path is a valid PDF within limits
    """

    def __init__(
        self,
        max_pages: int = 1000,
        max_size_mb: int = 50,
        target_pages: int = 800,
        min_pages: int = 50,
    ) -> None:
        self._max_pages = max_pages
        self._max_size_mb = max_size_mb
        self._target_pages = target_pages
        self._min_pages = min_pages

    def split(self, pdf_path: Path, output_dir: Path) -> SplitResult:
        """Split a PDF if it exceeds Gemini limits.

        Args:
            pdf_path: Path to the source PDF.
            output_dir: Directory to write chunk sub-PDFs into.

        Returns:
            SplitResult with one or more PDFChunks.
        """
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        doc = fitz.open(str(pdf_path))
        try:
            total_pages = len(doc)
            total_size_mb = pdf_path.stat().st_size / (1024 * 1024)

            logger.info(
                "pdf_split_check",
                path=str(pdf_path),
                total_pages=total_pages,
                size_mb=round(total_size_mb, 2),
            )

            # No split needed
            if total_pages <= self._max_pages and total_size_mb <= self._max_size_mb:
                logger.info("pdf_within_limits", total_pages=total_pages)
                chunk = PDFChunk(
                    chunk_index=0,
                    page_start=0,
                    page_end=total_pages - 1,
                    page_count=total_pages,
                    file_path=pdf_path,
                    file_size_mb=total_size_mb,
                )
                return SplitResult(
                    original_path=pdf_path,
                    total_pages=total_pages,
                    total_size_mb=total_size_mb,
                    was_split=False,
                    chunks=[chunk],
                )

            # Split needed
            toc = doc.get_toc(simple=True)  # [[level, title, page_num], ...]
            boundaries = self._compute_boundaries(total_pages, toc)

            output_dir.mkdir(parents=True, exist_ok=True)
            stem = pdf_path.stem
            chunks: list[PDFChunk] = []

            for i, (start, end) in enumerate(boundaries):
                chunk_path = output_dir / f"{stem}_chunk_{i:03d}.pdf"
                chunk_doc = fitz.open()
                chunk_doc.insert_pdf(doc, from_page=start, to_page=end)
                chunk_doc.save(str(chunk_path))
                chunk_doc.close()

                chunk_size_mb = chunk_path.stat().st_size / (1024 * 1024)

                # Identify which TOC sections start in this chunk
                sections = [
                    title for _, title, page in toc
                    if start <= (page - 1) <= end  # TOC pages are 1-indexed
                ]

                chunk = PDFChunk(
                    chunk_index=i,
                    page_start=start,
                    page_end=end,
                    page_count=end - start + 1,
                    file_path=chunk_path,
                    file_size_mb=round(chunk_size_mb, 2),
                    toc_sections=sections,
                )
                chunks.append(chunk)

                logger.info(
                    "pdf_chunk_created",
                    chunk=i,
                    pages=f"{start}-{end}",
                    page_count=chunk.page_count,
                    size_mb=chunk.file_size_mb,
                    sections=len(sections),
                )

            # Safety: verify all chunks are within limits
            for chunk in chunks:
                if chunk.page_count > self._max_pages:
                    logger.warning(
                        "chunk_exceeds_page_limit",
                        chunk=chunk.chunk_index,
                        pages=chunk.page_count,
                        max=self._max_pages,
                    )

            logger.info(
                "pdf_split_complete",
                total_pages=total_pages,
                num_chunks=len(chunks),
                chunk_pages=[c.page_count for c in chunks],
            )

            return SplitResult(
                original_path=pdf_path,
                total_pages=total_pages,
                total_size_mb=total_size_mb,
                was_split=True,
                chunks=chunks,
            )
        finally:
            doc.close()

    def _compute_boundaries(
        self,
        total_pages: int,
        toc: list[list],
    ) -> list[tuple[int, int]]:
        """Compute page range boundaries for splitting.

        Strategy:
        1. Collect TOC-based split candidates (level-1 section starts)
        2. Walk through pages in target_pages increments
        3. At each split point, prefer the nearest TOC boundary
        4. Ensure no chunk exceeds max_pages
        5. Merge chunks smaller than min_pages into the previous chunk

        Args:
            total_pages: Total pages in the document.
            toc: PyMuPDF TOC list [[level, title, 1-indexed-page], ...].

        Returns:
            List of (start_page, end_page) tuples, 0-indexed inclusive.
        """
        # Extract level-1 and level-2 TOC section start pages (0-indexed)
        toc_breaks: set[int] = set()
        for level, _title, page_num in toc:
            if level <= 2:  # Top-level sections only
                toc_breaks.add(page_num - 1)  # Convert to 0-indexed
        toc_breaks.discard(0)  # Never split at page 0

        sorted_breaks = sorted(toc_breaks)
        boundaries: list[tuple[int, int]] = []
        current_start = 0

        while current_start < total_pages:
            ideal_end = current_start + self._target_pages - 1

            # If remaining pages fit in one chunk, take them all
            if ideal_end >= total_pages - 1:
                boundaries.append((current_start, total_pages - 1))
                break

            # Find the nearest TOC break to the ideal split point
            split_at = self._find_nearest_break(
                ideal_end, sorted_breaks, current_start, total_pages
            )

            boundaries.append((current_start, split_at))
            current_start = split_at + 1

        # Merge tiny trailing chunks into the previous one
        boundaries = self._merge_small_chunks(boundaries)

        return boundaries

    def _find_nearest_break(
        self,
        ideal_end: int,
        sorted_breaks: list[int],
        current_start: int,
        total_pages: int,
    ) -> int:
        """Find the best page to split at, preferring TOC boundaries.

        Searches for a TOC break within ±20% of target_pages from ideal_end.
        Falls back to ideal_end if no TOC break is nearby.
        """
        search_range = int(self._target_pages * 0.2)
        search_min = max(current_start + self._min_pages, ideal_end - search_range)
        search_max = min(total_pages - 1, ideal_end + search_range)
        # Don't exceed the hard max_pages limit
        hard_max = current_start + self._max_pages - 1
        search_max = min(search_max, hard_max)

        # Find TOC breaks in the search window
        candidates = [b for b in sorted_breaks if search_min <= b <= search_max]

        if candidates:
            # Pick the one closest to ideal_end, but prefer splitting
            # right BEFORE a section start (so section starts at next chunk)
            best = min(candidates, key=lambda b: abs(b - ideal_end))
            return best - 1 if best > current_start else best

        # No TOC break nearby → split at ideal_end (or hard max)
        return min(ideal_end, hard_max)

    def _merge_small_chunks(
        self,
        boundaries: list[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        """Merge trailing chunks smaller than min_pages into the previous chunk."""
        if len(boundaries) <= 1:
            return boundaries

        merged: list[tuple[int, int]] = [boundaries[0]]
        for start, end in boundaries[1:]:
            chunk_pages = end - start + 1
            prev_start, prev_end = merged[-1]
            prev_pages = prev_end - prev_start + 1

            # If this chunk is too small AND merging won't exceed max_pages
            if chunk_pages < self._min_pages and (prev_pages + chunk_pages) <= self._max_pages:
                merged[-1] = (prev_start, end)
            else:
                merged.append((start, end))

        return merged
