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

    def __init__(self, use_ocr: bool = False) -> None:
        self._use_ocr = use_ocr

    async def extract(self, pdf_path: Path) -> list[PageBaseline]:
        """Extract baseline text for all pages in the PDF.

        Runs in a thread to avoid blocking the event loop.
        """
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        return await asyncio.to_thread(self._extract_sync, pdf_path)

    def _extract_sync(self, pdf_path: Path) -> list[PageBaseline]:
        """Synchronous extraction — called inside asyncio.to_thread.

        Fast path:
          1. Single fitz pass gets page metadata AND raw text length per page.
          2. Pages with enough raw text go into a single batched
             ``pymupdf4llm.to_markdown(pages=[...], page_chunks=True)`` call
             — layout analyzer + ONNX model load happens ONCE instead of
             once per page.
          3. Scanned / text-poor pages skip pymupdf4llm entirely (nothing
             for the layout extractor to do on them anyway).
        """
        pdf_str = str(pdf_path)

        doc = fitz.open(pdf_str)
        page_count = len(doc)
        page_meta: list[dict] = []
        text_page_indices: list[int] = []
        raw_text_by_page: dict[int, str] = {}

        for i in range(page_count):
            page = doc[i]
            raw_text = page.get_text().strip()
            page_meta.append({
                "image_count": len(page.get_images(full=True)),
                "width": page.rect.width,
                "height": page.rect.height,
            })
            raw_text_by_page[i] = raw_text
            if len(raw_text) >= SCANNED_THRESHOLD:
                text_page_indices.append(i)
        doc.close()

        # Batched layout-aware extraction — ONE pymupdf4llm call for every
        # text-bearing page in the document instead of one call per page.
        md_by_page: dict[int, str] = {}
        if text_page_indices:
            chunks = pymupdf4llm.to_markdown(
                pdf_str,
                pages=text_page_indices,
                page_chunks=True,
                use_ocr=self._use_ocr,
            )
            # chunks is a list aligned with text_page_indices.
            # Defensive: in some versions the chunk is a dict with "text";
            # fall back to raw text from fitz if the shape is unexpected.
            for idx, chunk in zip(text_page_indices, chunks):
                if isinstance(chunk, dict):
                    md_by_page[idx] = (chunk.get("text") or "").strip()
                else:
                    md_by_page[idx] = str(chunk).strip()

        baselines: list[PageBaseline] = []
        for page_num in range(page_count):
            md_text = md_by_page.get(page_num)
            if not md_text:
                # Scanned or pymupdf4llm produced nothing — fall back to
                # fitz raw text (still useful as a cross-check hint).
                md_text = raw_text_by_page.get(page_num, "")

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
                extraction_method=(
                    "pymupdf4llm" if page_num in md_by_page else "fitz_raw"
                ),
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
