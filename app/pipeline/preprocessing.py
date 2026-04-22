"""Phase 0: Baseline text extraction using pymupdf4llm.

Uses pymupdf4llm for layout-aware markdown extraction — handles multi-column
layouts, tables, reading order, heading detection, and image placeholders
out of the box.

This module produces a baseline text per page that the Validator (Phase 3)
uses to cross-check Gemini's extraction. Gemini is the primary extractor;
this is the **validation reference**.

Design notes:
- Runs entirely locally — no LLM calls, no GPU required
- pymupdf4llm includes layout analysis via onnxruntime
- Thread-safe: extraction runs in asyncio.to_thread
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import fitz  # PyMuPDF — for page metadata
import pymupdf4llm

from app.models.schemas import PageBaseline
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Pages with fewer characters than this are considered scanned/image-only
SCANNED_THRESHOLD = 50


class Preprocessor:
    """Extract layout-aware markdown from every page of a PDF.

    Usage:
        preprocessor = Preprocessor()
        baselines = await preprocessor.extract(Path("report.pdf"))
    """

    def __init__(self, ocr_lang: str = "en", use_ocr: bool = False) -> None:
        self._ocr_lang = ocr_lang
        self._use_ocr = use_ocr

    async def extract(self, pdf_path: Path) -> list[PageBaseline]:
        """Extract baseline text for all pages in the PDF.

        Runs in a thread to avoid blocking the event loop.
        """
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        return await asyncio.to_thread(self._extract_sync, pdf_path)

    def _extract_sync(self, pdf_path: Path) -> list[PageBaseline]:
        """Synchronous extraction — called inside asyncio.to_thread."""
        pdf_str = str(pdf_path)

        # Get page metadata via fitz (dimensions, image count)
        doc = fitz.open(pdf_str)
        page_meta = []
        for i in range(len(doc)):
            page = doc[i]
            page_meta.append({
                "image_count": len(page.get_images(full=True)),
                "width": page.rect.width,
                "height": page.rect.height,
            })
        page_count = len(doc)
        doc.close()

        # Extract markdown per page via pymupdf4llm
        baselines: list[PageBaseline] = []
        for page_num in range(page_count):
            md_text = pymupdf4llm.to_markdown(
                pdf_str,
                pages=[page_num],
                use_ocr=self._use_ocr,
            )

            # Strip trailing whitespace / page separators
            md_text = md_text.strip()

            meta = page_meta[page_num]
            is_scanned = len(md_text) < SCANNED_THRESHOLD

            baseline = PageBaseline(
                page_num=page_num,
                text=md_text,
                char_count=len(md_text),
                is_scanned=is_scanned,
                image_count=meta["image_count"],
                width=meta["width"],
                height=meta["height"],
                extraction_method="pymupdf4llm",
            )
            baselines.append(baseline)

            logger.info(
                "page_baseline_extracted",
                page=page_num,
                chars=baseline.char_count,
                method=baseline.extraction_method,
                is_scanned=baseline.is_scanned,
            )

        return baselines
