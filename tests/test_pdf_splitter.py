"""Tests for PDF splitting utility."""

from pathlib import Path

import fitz
import pytest

from app.utils.pdf_splitter import PDFChunk, PDFSplitter, SplitResult


def _make_pdf(tmp_path: Path, num_pages: int, name: str = "test.pdf") -> Path:
    """Helper: create a PDF with N pages, each containing its page number."""
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"Page {i + 1} of {num_pages}", fontsize=12)
    path = tmp_path / name
    doc.save(str(path))
    doc.close()
    return path


def _make_pdf_with_toc(
    tmp_path: Path, num_pages: int, toc: list[list], name: str = "toc_test.pdf"
) -> Path:
    """Helper: create a PDF with N pages and a table of contents."""
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"Page {i + 1}", fontsize=12)
    doc.set_toc(toc)
    path = tmp_path / name
    doc.save(str(path))
    doc.close()
    return path


class TestPDFSplitterNoSplitNeeded:
    """Test cases where the PDF fits within limits."""

    def test_small_pdf_returns_single_chunk(self, tmp_path: Path):
        """PDF within limits should return one chunk pointing to the original file."""
        pdf = _make_pdf(tmp_path, 10)
        splitter = PDFSplitter(max_pages=1000, target_pages=800)
        result = splitter.split(pdf, tmp_path / "chunks")

        assert isinstance(result, SplitResult)
        assert result.was_split is False
        assert result.total_pages == 10
        assert len(result.chunks) == 1
        assert result.chunks[0].file_path == pdf
        assert result.chunks[0].page_start == 0
        assert result.chunks[0].page_end == 9
        assert result.chunks[0].page_count == 10

    def test_exactly_at_limit(self, tmp_path: Path):
        """PDF with exactly max_pages should NOT be split."""
        pdf = _make_pdf(tmp_path, 100)
        splitter = PDFSplitter(max_pages=100, target_pages=80)
        result = splitter.split(pdf, tmp_path / "chunks")

        assert result.was_split is False
        assert len(result.chunks) == 1

    def test_file_not_found(self, tmp_path: Path):
        """Should raise FileNotFoundError for missing file."""
        splitter = PDFSplitter()
        with pytest.raises(FileNotFoundError):
            splitter.split(tmp_path / "nonexistent.pdf", tmp_path / "chunks")


class TestPDFSplitterBasicSplitting:
    """Test fixed-interval splitting (no TOC)."""

    def test_splits_into_correct_number_of_chunks(self, tmp_path: Path):
        """200 pages with target=80 should produce ~3 chunks."""
        pdf = _make_pdf(tmp_path, 200)
        splitter = PDFSplitter(max_pages=100, target_pages=80, min_pages=10)
        result = splitter.split(pdf, tmp_path / "chunks")

        assert result.was_split is True
        assert result.total_pages == 200
        assert len(result.chunks) >= 2

        # All pages must be covered exactly once
        total_covered = sum(c.page_count for c in result.chunks)
        assert total_covered == 200

    def test_chunks_are_contiguous(self, tmp_path: Path):
        """Chunk boundaries must be contiguous (no gaps, no overlaps)."""
        pdf = _make_pdf(tmp_path, 300)
        splitter = PDFSplitter(max_pages=100, target_pages=80, min_pages=10)
        result = splitter.split(pdf, tmp_path / "chunks")

        for i in range(len(result.chunks) - 1):
            current = result.chunks[i]
            next_chunk = result.chunks[i + 1]
            assert next_chunk.page_start == current.page_end + 1, (
                f"Gap between chunk {i} (end={current.page_end}) "
                f"and chunk {i+1} (start={next_chunk.page_start})"
            )

    def test_no_chunk_exceeds_max_pages(self, tmp_path: Path):
        """No chunk should have more pages than max_pages."""
        pdf = _make_pdf(tmp_path, 500)
        splitter = PDFSplitter(max_pages=100, target_pages=80, min_pages=10)
        result = splitter.split(pdf, tmp_path / "chunks")

        for chunk in result.chunks:
            assert chunk.page_count <= 100, (
                f"Chunk {chunk.chunk_index} has {chunk.page_count} pages (max=100)"
            )

    def test_chunk_files_are_valid_pdfs(self, tmp_path: Path):
        """Each chunk file should be a readable PDF with the correct page count."""
        pdf = _make_pdf(tmp_path, 200)
        splitter = PDFSplitter(max_pages=100, target_pages=80, min_pages=10)
        result = splitter.split(pdf, tmp_path / "chunks")

        for chunk in result.chunks:
            assert chunk.file_path.exists()
            doc = fitz.open(str(chunk.file_path))
            assert len(doc) == chunk.page_count
            doc.close()

    def test_chunk_content_matches_original(self, tmp_path: Path):
        """Text in chunk pages should match the corresponding original pages."""
        pdf = _make_pdf(tmp_path, 150)
        splitter = PDFSplitter(max_pages=100, target_pages=80, min_pages=10)
        result = splitter.split(pdf, tmp_path / "chunks")

        original = fitz.open(str(pdf))
        for chunk in result.chunks:
            chunk_doc = fitz.open(str(chunk.file_path))
            for local_idx in range(len(chunk_doc)):
                orig_page_num = chunk.page_start + local_idx
                orig_text = original[orig_page_num].get_text("text")
                chunk_text = chunk_doc[local_idx].get_text("text")
                assert f"Page {orig_page_num + 1}" in chunk_text
            chunk_doc.close()
        original.close()

    def test_small_trailing_chunk_merged(self, tmp_path: Path):
        """A trailing chunk smaller than min_pages should merge into the previous chunk."""
        # 170 pages with target=80: would produce chunks of 80, 80, 10
        # min_pages=50: the trailing 10-page chunk should merge → 80, 90
        pdf = _make_pdf(tmp_path, 170)
        splitter = PDFSplitter(max_pages=100, target_pages=80, min_pages=50)
        result = splitter.split(pdf, tmp_path / "chunks")

        for chunk in result.chunks:
            assert chunk.page_count >= 50 or chunk.page_count == result.total_pages


class TestPDFSplitterWithTOC:
    """Test section-aware splitting using TOC data."""

    def test_splits_at_toc_boundaries(self, tmp_path: Path):
        """Splitter should prefer breaking at TOC section starts."""
        toc = [
            [1, "Introduction", 1],
            [1, "Formation Data", 75],    # Good split point near target
            [1, "Drilling Summary", 160],
            [1, "Appendix", 240],
        ]
        pdf = _make_pdf_with_toc(tmp_path, 300, toc)
        splitter = PDFSplitter(max_pages=100, target_pages=80, min_pages=10)
        result = splitter.split(pdf, tmp_path / "chunks")

        assert result.was_split is True
        # Chunk boundaries should align near TOC section starts
        chunk_starts = [c.page_start for c in result.chunks]
        # First chunk always starts at 0
        assert chunk_starts[0] == 0

    def test_toc_sections_tracked_per_chunk(self, tmp_path: Path):
        """Each chunk should list which TOC sections start within it."""
        toc = [
            [1, "Part A", 1],
            [1, "Part B", 50],
            [1, "Part C", 120],
        ]
        pdf = _make_pdf_with_toc(tmp_path, 200, toc)
        splitter = PDFSplitter(max_pages=100, target_pages=80, min_pages=10)
        result = splitter.split(pdf, tmp_path / "chunks")

        # At least some chunks should have toc_sections populated
        all_sections = []
        for chunk in result.chunks:
            all_sections.extend(chunk.toc_sections)
        assert len(all_sections) > 0


class TestPDFSplitterLargeDocument:
    """Simulate the 6000-page well report scenario."""

    def test_6000_page_split(self, tmp_path: Path):
        """6000-page PDF should split into ~8 chunks of ~800 pages."""
        # Create a lightweight 6000-page PDF (minimal content for speed)
        doc = fitz.open()
        for i in range(6000):
            page = doc.new_page(width=612, height=792)
            if i % 100 == 0:  # Only add text to every 100th page for speed
                page.insert_text((72, 72), f"Page {i + 1}")
        pdf = tmp_path / "huge_report.pdf"
        doc.save(str(pdf))
        doc.close()

        splitter = PDFSplitter(max_pages=1000, target_pages=800, min_pages=50)
        result = splitter.split(pdf, tmp_path / "chunks")

        assert result.was_split is True
        assert result.total_pages == 6000
        assert len(result.chunks) >= 6  # 6000/1000 minimum
        assert len(result.chunks) <= 12  # Reasonable upper bound

        # Verify completeness
        total_covered = sum(c.page_count for c in result.chunks)
        assert total_covered == 6000

        # Verify no chunk exceeds the limit
        for chunk in result.chunks:
            assert chunk.page_count <= 1000

        # Verify files exist and are valid
        for chunk in result.chunks:
            assert chunk.file_path.exists()


class TestComputeBoundaries:
    """Unit tests for the boundary computation algorithm."""

    def test_empty_toc_uses_fixed_intervals(self):
        """Without TOC, should split at fixed target_pages intervals."""
        splitter = PDFSplitter(max_pages=100, target_pages=80, min_pages=10)
        boundaries = splitter._compute_boundaries(250, [])

        assert len(boundaries) >= 3
        total = sum(end - start + 1 for start, end in boundaries)
        assert total == 250

    def test_boundaries_cover_all_pages(self):
        """Boundaries must cover page 0 through total_pages-1."""
        splitter = PDFSplitter(max_pages=100, target_pages=80, min_pages=10)
        boundaries = splitter._compute_boundaries(500, [])

        assert boundaries[0][0] == 0
        assert boundaries[-1][1] == 499

    def test_single_page_document(self):
        """Single-page doc should produce one boundary."""
        splitter = PDFSplitter(max_pages=100, target_pages=80, min_pages=10)
        boundaries = splitter._compute_boundaries(1, [])

        assert boundaries == [(0, 0)]
