"""Pydantic models for requests, responses, and internal data structures."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ContentType(str, Enum):
    TEXT = "text"
    TABLE = "table"
    DIAGRAM = "diagram"
    LOG_CHART = "log_chart"
    MIXED = "mixed"


class Complexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ---------------------------------------------------------------------------
# Document Structure (Phase 1 output)
# ---------------------------------------------------------------------------

class SectionInfo(BaseModel):
    """A logical section of the document (e.g., 'Formation Evaluation')."""
    name: str
    page_start: int
    page_end: int
    content_types: list[ContentType] = []
    complexity: Complexity = Complexity.MEDIUM


class CrossPageTable(BaseModel):
    """A table that spans multiple pages."""
    page_start: int
    page_end: int
    description: str = ""


class KeyEntities(BaseModel):
    """Document-level metadata extracted from the structure scan."""
    well_name: str = ""
    operator: str = ""
    field: str = ""
    dates: list[str] = []


class DocStructure(BaseModel):
    """Top-level output of the Phase 1 document structure scan."""
    document_type: str = ""
    title: str = ""
    sections: list[SectionInfo] = []
    cross_page_tables: list[CrossPageTable] = []
    key_entities: KeyEntities = Field(default_factory=KeyEntities)


# ---------------------------------------------------------------------------
# Page-level extraction results
# ---------------------------------------------------------------------------

class TableResult(BaseModel):
    """A single table extracted from a page."""
    id: str = ""
    markdown: str = ""
    json_data: dict[str, Any] = {}
    continues_on_next_page: bool = False


class ImageResult(BaseModel):
    """An image/diagram described from a page."""
    id: str = ""
    description: str = ""
    location: str = ""


class PageExtraction(BaseModel):
    """Extraction result for a single page."""
    page_num: int
    markdown: str = ""
    tables: list[TableResult] = []
    images: list[ImageResult] = []
    cross_page_notes: str = ""


# ---------------------------------------------------------------------------
# Validation results
# ---------------------------------------------------------------------------

class PageValidation(BaseModel):
    """Validation result for a single page."""
    page_num: int
    passed: bool = True
    confidence: float = 1.0
    gaps: list[str] = []


class BatchValidation(BaseModel):
    """Validation result for an entire batch of pages."""
    passed: bool = True
    overall_confidence: float = 1.0
    page_results: list[PageValidation] = []


# ---------------------------------------------------------------------------
# Baseline text (Phase 0 output)
# ---------------------------------------------------------------------------

class PageBaseline(BaseModel):
    """Baseline text for a single page from PyMuPDF / PaddleOCR."""
    page_num: int
    text: str = ""
    char_count: int = 0
    is_scanned: bool = False
    image_count: int = 0
    width: float = 0.0
    height: float = 0.0
    extraction_method: str = "pymupdf"  # "pymupdf" | "paddleocr"


# ---------------------------------------------------------------------------
# Batch (Phase 2)
# ---------------------------------------------------------------------------

class PageBatch(BaseModel):
    """A batch of pages to be processed together."""
    batch_id: int
    page_start: int
    page_end: int
    section_context: str = ""
    complexity: Complexity = Complexity.MEDIUM
    baseline_texts: list[PageBaseline] = []
    media_resolution: str = "MEDIUM"  # "HIGH" | "MEDIUM" | "LOW"


# ---------------------------------------------------------------------------
# API request / response
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    """Request body for POST /ingest."""
    filename: str = ""


class IngestResponse(BaseModel):
    """Response body for POST /ingest."""
    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: JobStatus = JobStatus.PENDING
    message: str = ""


class JobStatusResponse(BaseModel):
    """Response body for GET /ingest/{job_id}."""
    job_id: str
    status: JobStatus
    total_pages: int = 0
    pages_processed: int = 0
    current_phase: str = ""
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Trace / logging
# ---------------------------------------------------------------------------

class TraceEntry(BaseModel):
    """A single trace log entry for pipeline observability."""
    phase: str
    model: str = ""
    page: int | None = None
    time_ms: int = 0
    cached: bool = False
    details: dict[str, Any] = {}
