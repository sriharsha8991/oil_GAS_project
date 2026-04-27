"""Pydantic models for requests, responses, and internal data structures."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Phase 0: baseline text
# ---------------------------------------------------------------------------

class PageBaseline(BaseModel):
    """Baseline text for a single page from pymupdf4llm."""
    page_num: int
    text: str = ""
    char_count: int = 0
    is_scanned: bool = False
    image_count: int = 0
    width: float = 0.0
    height: float = 0.0
    extraction_method: str = "pymupdf4llm"


# ---------------------------------------------------------------------------
# Phase 1: rasterized page image (streamed to workers)
# ---------------------------------------------------------------------------

class PageImage(BaseModel):
    """A rasterized PDF page ready for Gemini vision extraction."""
    page_num: int
    png_bytes: bytes
    width: int = 0
    height: int = 0
    dpi: int = 200
    is_scanned: bool = False
    baseline_text: str = ""
    png_path: str | None = None  # On-disk persisted PNG (set by rasterizer)

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Phase 2: per-page extraction & validation results
# ---------------------------------------------------------------------------

class TableResult(BaseModel):
    """A single table extracted from a page."""
    id: str = ""
    markdown: str = ""
    json_data: dict[str, Any] = {}
    continues_on_next_page: bool = False
    bbox: list[int] | None = None  # [ymin, xmin, ymax, xmax] normalized to 0-1000


class ImageResult(BaseModel):
    """An image/diagram described from a page."""
    id: str = ""
    description: str = ""
    location: str = ""
    image_type: str = ""  # log_strip | schematic | plot | photo | table_image | other
    bbox: list[int] | None = None  # [ymin, xmin, ymax, xmax] normalized to 0-1000


class Entity(BaseModel):
    """A structured well-domain entity extracted from a page.

    Loose schema for Phase 1A. Phase 1B will tighten with controlled vocab
    (IADC NPT codes, depth types, units) via Gemini responseSchema enums.
    """
    type: str = ""           # well_id | depth | formation | npt_event | fluid | bha | date | other
    value: str = ""          # canonical text representation
    unit: str = ""           # ft | m | ppg | psi | hours | usd | etc
    context: str = ""        # short surrounding phrase for disambiguation
    bbox: list[int] | None = None  # [ymin, xmin, ymax, xmax] normalized to 0-1000


class PageExtraction(BaseModel):
    """Extraction result for a single page."""
    page_num: int
    markdown: str = ""
    tables: list[TableResult] = []
    images: list[ImageResult] = []
    entities: list[Entity] = []
    cross_page_notes: str = ""


class PageValidation(BaseModel):
    """Validation result for a single page."""
    page_num: int
    passed: bool = True
    confidence: float = 1.0
    gaps: list[str] = []



