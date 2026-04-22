"""Phase 1: Document Structure Scan via Gemini.

Uploads the PDF to the Gemini Files API and makes a single low-cost call
(MEDIA_RESOLUTION_LOW) to extract the document's logical structure:
  - Document type, title, key entities
  - Section boundaries with page ranges and complexity tags
  - Cross-page tables

The result (DocStructure) drives downstream batching, prompt context,
media resolution selection, and Qdrant metadata.

An explicit Gemini cache of the uploaded PDF is created here and reused
by all subsequent extractor/validator calls (~4x input cost reduction).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import Settings
from app.models.schemas import (
    Complexity,
    ContentType,
    CrossPageTable,
    DocStructure,
    KeyEntities,
    SectionInfo,
)
from app.utils.gemini_client import GeminiClient
from app.utils.logging import get_logger
from app.utils.pdf_uploader import PDFUploader, UploadResult

logger = get_logger(__name__)

# The system instruction baked into the cache — reused by all Phase 3 calls.
CACHE_SYSTEM_INSTRUCTION = (
    "You are a specialist document extraction agent for geological well reports. "
    "You extract text, tables, diagrams, and metadata from PDF pages with high accuracy. "
    "Always preserve exact numbers, depths, coordinates, dates, and technical terminology."
)

# Prompt for the structure scan call itself.
STRUCTURE_SCAN_PROMPT = """\
Analyze this document's structure. Return a JSON object with exactly this schema \
(no markdown fences, just raw JSON):

{
  "document_type": "<string: e.g. well_completion_report, drilling_log, seismic_survey>",
  "title": "<string: full document title>",
  "sections": [
    {
      "name": "<string: section name>",
      "page_start": <int: 0-indexed first page>,
      "page_end": <int: 0-indexed last page (inclusive)>,
      "content_types": ["text", "table", "diagram", "log_chart"],
      "complexity": "low" | "medium" | "high"
    }
  ],
  "cross_page_tables": [
    {
      "page_start": <int: 0-indexed>,
      "page_end": <int: 0-indexed>,
      "description": "<string: brief description of the table>"
    }
  ],
  "key_entities": {
    "well_name": "<string>",
    "operator": "<string>",
    "field": "<string>",
    "dates": ["<string>"]
  }
}

Rules:
- Page numbers are 0-indexed (first page = 0).
- Every page must belong to at least one section.
- content_types: only use "text", "table", "diagram", "log_chart".
- complexity: "high" for pages with dense tables, charts, or diagrams; \
"low" for mostly text; "medium" otherwise.
- cross_page_tables: list tables that span more than one page.
- key_entities: extract the well name, operator, field/basin, and important dates.
- Return ONLY the JSON object, no extra text.
"""


class StructureScanner:
    """Runs the Phase 1 document structure scan.

    Usage:
        scanner = StructureScanner(gemini_client, settings)
        result = await scanner.scan(pdf_path, doc_id, total_pages)
        result.doc_structure   # DocStructure
        result.upload_result   # UploadResult (file + cache refs)
    """

    def __init__(self, client: GeminiClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._uploader = PDFUploader(client)

    async def scan(
        self,
        pdf_path: Path,
        doc_id: str,
        total_pages: int,
    ) -> ScanResult:
        """Upload PDF, create cache, and extract document structure.

        Args:
            pdf_path: Path to the PDF (or split chunk) on disk.
            doc_id: Unique document identifier.
            total_pages: Total page count (for validation).

        Returns:
            ScanResult with DocStructure and UploadResult.
        """
        logger.info("structure_scan_start", doc_id=doc_id, total_pages=total_pages)

        # Step 1: Upload PDF and create explicit cache
        upload_result = await self._uploader.upload_and_cache(
            pdf_path=pdf_path,
            model=self._settings.extraction_model,
            system_instruction=CACHE_SYSTEM_INSTRUCTION,
            doc_id=doc_id,
        )

        # Step 2: Call Gemini with the uploaded file for structure extraction
        raw_json = await self._client.generate(
            model=self._settings.extraction_model,
            contents=[upload_result.file, STRUCTURE_SCAN_PROMPT],
            system_instruction=(
                "You are a document structure analyzer. "
                "Return only valid JSON, no markdown fences or extra text."
            ),
            response_mime_type="application/json",
            temperature=0.0,
        )

        # Step 3: Parse response into DocStructure
        doc_structure = _parse_structure(raw_json, total_pages)

        logger.info(
            "structure_scan_done",
            doc_id=doc_id,
            document_type=doc_structure.document_type,
            sections=len(doc_structure.sections),
            cross_page_tables=len(doc_structure.cross_page_tables),
        )

        return ScanResult(doc_structure=doc_structure, upload_result=upload_result)


class ScanResult:
    """Container for the Phase 1 output."""

    def __init__(self, doc_structure: DocStructure, upload_result: UploadResult) -> None:
        self.doc_structure = doc_structure
        self.upload_result = upload_result

    @property
    def cache_name(self) -> str:
        """Shortcut to the cache resource name for downstream calls."""
        return self.upload_result.cache_name


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_structure(raw_json: str, total_pages: int) -> DocStructure:
    """Parse the raw JSON response into a validated DocStructure.

    Tolerant of minor issues: strips markdown fences, clamps page numbers,
    maps unknown content types to defaults.
    """
    cleaned = _strip_json_fences(raw_json)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("structure_scan_parse_error", error=str(exc), raw=cleaned[:500])
        # Return a minimal fallback structure covering all pages
        return _fallback_structure(total_pages)

    return _build_doc_structure(data, total_pages)


def _strip_json_fences(text: str) -> str:
    """Remove markdown code fences if present."""
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        first_newline = text.index("\n") if "\n" in text else len(text)
        text = text[first_newline + 1:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _build_doc_structure(data: dict[str, Any], total_pages: int) -> DocStructure:
    """Build a DocStructure from parsed JSON, with validation and clamping."""
    max_page = total_pages - 1  # 0-indexed

    # Parse sections
    sections: list[SectionInfo] = []
    for s in data.get("sections", []):
        content_types = _parse_content_types(s.get("content_types", []))
        complexity = _parse_complexity(s.get("complexity", "medium"))
        sections.append(
            SectionInfo(
                name=s.get("name", "Unknown"),
                page_start=_clamp(s.get("page_start", 0), 0, max_page),
                page_end=_clamp(s.get("page_end", max_page), 0, max_page),
                content_types=content_types,
                complexity=complexity,
            )
        )

    # If no sections parsed, create a fallback covering all pages
    if not sections:
        sections = [
            SectionInfo(
                name="Full Document",
                page_start=0,
                page_end=max_page,
                content_types=[ContentType.TEXT],
                complexity=Complexity.MEDIUM,
            )
        ]

    # Parse cross-page tables
    cross_page_tables: list[CrossPageTable] = []
    for t in data.get("cross_page_tables", []):
        cross_page_tables.append(
            CrossPageTable(
                page_start=_clamp(t.get("page_start", 0), 0, max_page),
                page_end=_clamp(t.get("page_end", 0), 0, max_page),
                description=t.get("description", ""),
            )
        )

    # Parse key entities
    ke_data = data.get("key_entities", {})
    key_entities = KeyEntities(
        well_name=ke_data.get("well_name", ""),
        operator=ke_data.get("operator", ""),
        field=ke_data.get("field", ""),
        dates=ke_data.get("dates", []),
    )

    return DocStructure(
        document_type=data.get("document_type", ""),
        title=data.get("title", ""),
        sections=sections,
        cross_page_tables=cross_page_tables,
        key_entities=key_entities,
    )


def _fallback_structure(total_pages: int) -> DocStructure:
    """Return a minimal DocStructure when parsing fails."""
    return DocStructure(
        document_type="unknown",
        title="",
        sections=[
            SectionInfo(
                name="Full Document",
                page_start=0,
                page_end=max(0, total_pages - 1),
                content_types=[ContentType.TEXT],
                complexity=Complexity.MEDIUM,
            )
        ],
    )


def _parse_content_types(values: list[str]) -> list[ContentType]:
    """Map raw strings to ContentType enum, skipping unknowns."""
    mapping = {
        "text": ContentType.TEXT,
        "table": ContentType.TABLE,
        "diagram": ContentType.DIAGRAM,
        "log_chart": ContentType.LOG_CHART,
    }
    result = []
    for v in values:
        ct = mapping.get(v.lower().strip())
        if ct:
            result.append(ct)
    return result or [ContentType.TEXT]


def _parse_complexity(value: str) -> Complexity:
    """Map a raw string to a Complexity enum."""
    mapping = {
        "low": Complexity.LOW,
        "medium": Complexity.MEDIUM,
        "high": Complexity.HIGH,
    }
    return mapping.get(value.lower().strip(), Complexity.MEDIUM)


def _clamp(value: int, lo: int, hi: int) -> int:
    """Clamp an integer to [lo, hi]."""
    return max(lo, min(hi, value))
