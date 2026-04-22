"""Tests for Phase 0: Baseline text extraction (preprocessing via pymupdf4llm)."""

from pathlib import Path

import pytest

from app.models.schemas import PageBaseline
from app.pipeline.preprocessing import SCANNED_THRESHOLD, Preprocessor


class TestPreprocessor:
    """Tests for Preprocessor.extract()."""

    @pytest.mark.asyncio
    async def test_extract_returns_baselines_for_all_pages(self, sample_pdf_path: Path):
        """Should return one PageBaseline per page in the PDF."""
        preprocessor = Preprocessor(use_ocr=False)
        baselines = await preprocessor.extract(sample_pdf_path)

        assert len(baselines) == 3
        assert all(isinstance(b, PageBaseline) for b in baselines)

    @pytest.mark.asyncio
    async def test_page_numbers_are_sequential(self, sample_pdf_path: Path):
        """Page numbers should be 0-indexed and sequential."""
        preprocessor = Preprocessor(use_ocr=False)
        baselines = await preprocessor.extract(sample_pdf_path)

        page_nums = [b.page_num for b in baselines]
        assert page_nums == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_text_extraction_from_text_page(self, sample_pdf_path: Path):
        """Pages with embedded text should have non-empty text."""
        preprocessor = Preprocessor(use_ocr=False)
        baselines = await preprocessor.extract(sample_pdf_path)

        page0 = baselines[0]
        assert "Well Completion Report" in page0.text
        assert "ABC-1" in page0.text
        assert page0.char_count > SCANNED_THRESHOLD
        assert page0.extraction_method == "pymupdf4llm"
        assert page0.is_scanned is False

    @pytest.mark.asyncio
    async def test_table_like_text_extraction(self, sample_pdf_path: Path):
        """Page with table-like text should extract content."""
        preprocessor = Preprocessor(use_ocr=False)
        baselines = await preprocessor.extract(sample_pdf_path)

        page1 = baselines[1]
        assert "Formation Evaluation" in page1.text
        assert "Depth" in page1.text
        assert "3000" in page1.text

    @pytest.mark.asyncio
    async def test_sparse_page_detected_as_scanned(self, sample_pdf_path: Path):
        """Page with minimal text should be flagged as scanned."""
        preprocessor = Preprocessor(use_ocr=False)
        baselines = await preprocessor.extract(sample_pdf_path)

        page2 = baselines[2]
        assert page2.is_scanned is True

    @pytest.mark.asyncio
    async def test_blank_page_detected_as_scanned(self, empty_pdf_path: Path):
        """Completely blank page should be flagged as scanned."""
        preprocessor = Preprocessor(use_ocr=False)
        baselines = await preprocessor.extract(empty_pdf_path)

        assert len(baselines) == 1
        assert baselines[0].is_scanned is True
        assert baselines[0].char_count < SCANNED_THRESHOLD

    @pytest.mark.asyncio
    async def test_page_dimensions_are_populated(self, sample_pdf_path: Path):
        """Page width and height should be set (US Letter = 612x792)."""
        preprocessor = Preprocessor(use_ocr=False)
        baselines = await preprocessor.extract(sample_pdf_path)

        for b in baselines:
            assert b.width == pytest.approx(612, abs=1)
            assert b.height == pytest.approx(792, abs=1)

    @pytest.mark.asyncio
    async def test_file_not_found_raises(self, tmp_path: Path):
        """Should raise FileNotFoundError for missing PDF."""
        preprocessor = Preprocessor(use_ocr=False)
        with pytest.raises(FileNotFoundError):
            await preprocessor.extract(tmp_path / "nonexistent.pdf")

    @pytest.mark.asyncio
    async def test_extraction_method_is_pymupdf4llm(self, sample_pdf_path: Path):
        """All pages should use pymupdf4llm extraction method."""
        preprocessor = Preprocessor(use_ocr=False)
        baselines = await preprocessor.extract(sample_pdf_path)

        for b in baselines:
            assert b.extraction_method == "pymupdf4llm"


class TestPreprocessorSync:
    """Test the synchronous internals directly."""

    def test_extract_sync_returns_correct_count(self, sample_pdf_path: Path):
        """_extract_sync should return baselines matching page count."""
        preprocessor = Preprocessor(use_ocr=False)
        baselines = preprocessor._extract_sync(sample_pdf_path)

        assert len(baselines) == 3

    def test_extract_sync_populates_image_count(self, sample_pdf_path: Path):
        """Image count should be >= 0 for all pages."""
        preprocessor = Preprocessor(use_ocr=False)
        baselines = preprocessor._extract_sync(sample_pdf_path)

        for b in baselines:
            assert b.image_count >= 0
