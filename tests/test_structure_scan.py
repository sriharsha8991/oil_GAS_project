"""Tests for Phase 1: Document Structure Scan.

Tests the parsing/validation logic locally (no Gemini API calls).
The StructureScanner.scan() integration is tested with mocked Gemini responses.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.models.schemas import (
    Complexity,
    ContentType,
    CrossPageTable,
    DocStructure,
    KeyEntities,
    SectionInfo,
)
from app.pipeline.structure_scan import (
    CACHE_SYSTEM_INSTRUCTION,
    STRUCTURE_SCAN_PROMPT,
    ScanResult,
    StructureScanner,
    _build_doc_structure,
    _clamp,
    _fallback_structure,
    _parse_complexity,
    _parse_content_types,
    _parse_structure,
    _strip_json_fences,
)


# ---------------------------------------------------------------------------
# Sample Gemini responses for mocking
# ---------------------------------------------------------------------------

SAMPLE_STRUCTURE_JSON = {
    "document_type": "well_completion_report",
    "title": "GSWA Harvey 1 Well Completion Report",
    "sections": [
        {
            "name": "Title Page",
            "page_start": 0,
            "page_end": 0,
            "content_types": ["text", "diagram"],
            "complexity": "low",
        },
        {
            "name": "Abstract",
            "page_start": 1,
            "page_end": 2,
            "content_types": ["text"],
            "complexity": "low",
        },
        {
            "name": "Formation Evaluation",
            "page_start": 3,
            "page_end": 8,
            "content_types": ["text", "table", "diagram"],
            "complexity": "high",
        },
        {
            "name": "Core Analysis",
            "page_start": 9,
            "page_end": 14,
            "content_types": ["text", "table"],
            "complexity": "medium",
        },
    ],
    "cross_page_tables": [
        {
            "page_start": 5,
            "page_end": 7,
            "description": "Porosity and permeability analysis results",
        }
    ],
    "key_entities": {
        "well_name": "GSWA Harvey 1",
        "operator": "Geological Survey of Western Australia",
        "field": "Perth Basin",
        "dates": ["2012-02-07", "2012-03-26"],
    },
}


# ---------------------------------------------------------------------------
# _strip_json_fences
# ---------------------------------------------------------------------------


class TestStripJsonFences:
    def test_no_fences(self):
        assert _strip_json_fences('{"a": 1}') == '{"a": 1}'

    def test_json_fence(self):
        text = '```json\n{"a": 1}\n```'
        assert _strip_json_fences(text) == '{"a": 1}'

    def test_plain_fence(self):
        text = '```\n{"a": 1}\n```'
        assert _strip_json_fences(text) == '{"a": 1}'

    def test_whitespace(self):
        text = '  \n```json\n{"a": 1}\n```  \n'
        assert _strip_json_fences(text) == '{"a": 1}'


# ---------------------------------------------------------------------------
# _parse_content_types
# ---------------------------------------------------------------------------


class TestParseContentTypes:
    def test_valid_types(self):
        result = _parse_content_types(["text", "table", "diagram", "log_chart"])
        assert result == [
            ContentType.TEXT,
            ContentType.TABLE,
            ContentType.DIAGRAM,
            ContentType.LOG_CHART,
        ]

    def test_unknown_types_skipped(self):
        result = _parse_content_types(["text", "unknown_type", "table"])
        assert result == [ContentType.TEXT, ContentType.TABLE]

    def test_empty_returns_text_default(self):
        assert _parse_content_types([]) == [ContentType.TEXT]

    def test_all_unknown_returns_text_default(self):
        assert _parse_content_types(["foo", "bar"]) == [ContentType.TEXT]

    def test_case_insensitive(self):
        result = _parse_content_types(["TEXT", "Table", "DIAGRAM"])
        assert result == [ContentType.TEXT, ContentType.TABLE, ContentType.DIAGRAM]

    def test_whitespace_trimmed(self):
        result = _parse_content_types([" text ", " table "])
        assert result == [ContentType.TEXT, ContentType.TABLE]


# ---------------------------------------------------------------------------
# _parse_complexity
# ---------------------------------------------------------------------------


class TestParseComplexity:
    def test_valid_values(self):
        assert _parse_complexity("low") == Complexity.LOW
        assert _parse_complexity("medium") == Complexity.MEDIUM
        assert _parse_complexity("high") == Complexity.HIGH

    def test_case_insensitive(self):
        assert _parse_complexity("HIGH") == Complexity.HIGH
        assert _parse_complexity("Low") == Complexity.LOW

    def test_unknown_defaults_to_medium(self):
        assert _parse_complexity("extreme") == Complexity.MEDIUM
        assert _parse_complexity("") == Complexity.MEDIUM


# ---------------------------------------------------------------------------
# _clamp
# ---------------------------------------------------------------------------


class TestClamp:
    def test_within_range(self):
        assert _clamp(5, 0, 10) == 5

    def test_below_min(self):
        assert _clamp(-1, 0, 10) == 0

    def test_above_max(self):
        assert _clamp(15, 0, 10) == 10

    def test_at_boundaries(self):
        assert _clamp(0, 0, 10) == 0
        assert _clamp(10, 0, 10) == 10


# ---------------------------------------------------------------------------
# _fallback_structure
# ---------------------------------------------------------------------------


class TestFallbackStructure:
    def test_covers_all_pages(self):
        ds = _fallback_structure(20)
        assert ds.document_type == "unknown"
        assert len(ds.sections) == 1
        assert ds.sections[0].page_start == 0
        assert ds.sections[0].page_end == 19
        assert ds.sections[0].name == "Full Document"

    def test_single_page(self):
        ds = _fallback_structure(1)
        assert ds.sections[0].page_start == 0
        assert ds.sections[0].page_end == 0

    def test_no_cross_page_tables(self):
        ds = _fallback_structure(10)
        assert ds.cross_page_tables == []


# ---------------------------------------------------------------------------
# _build_doc_structure
# ---------------------------------------------------------------------------


class TestBuildDocStructure:
    def test_full_parse(self):
        ds = _build_doc_structure(SAMPLE_STRUCTURE_JSON, total_pages=15)
        assert ds.document_type == "well_completion_report"
        assert ds.title == "GSWA Harvey 1 Well Completion Report"
        assert len(ds.sections) == 4
        assert ds.sections[0].name == "Title Page"
        assert ds.sections[2].complexity == Complexity.HIGH
        assert ContentType.TABLE in ds.sections[2].content_types
        assert len(ds.cross_page_tables) == 1
        assert ds.cross_page_tables[0].description == "Porosity and permeability analysis results"
        assert ds.key_entities.well_name == "GSWA Harvey 1"
        assert ds.key_entities.operator == "Geological Survey of Western Australia"
        assert len(ds.key_entities.dates) == 2

    def test_page_numbers_clamped(self):
        """Page numbers beyond total_pages should be clamped."""
        data = {
            "sections": [
                {"name": "Test", "page_start": -5, "page_end": 100},
            ]
        }
        ds = _build_doc_structure(data, total_pages=10)
        assert ds.sections[0].page_start == 0
        assert ds.sections[0].page_end == 9

    def test_empty_sections_gets_fallback(self):
        data = {"document_type": "test", "sections": []}
        ds = _build_doc_structure(data, total_pages=5)
        assert len(ds.sections) == 1
        assert ds.sections[0].name == "Full Document"
        assert ds.sections[0].page_end == 4

    def test_missing_fields_default(self):
        data = {}
        ds = _build_doc_structure(data, total_pages=3)
        assert ds.document_type == ""
        assert ds.title == ""
        assert ds.key_entities.well_name == ""
        assert len(ds.sections) == 1  # fallback

    def test_cross_page_tables_clamped(self):
        data = {
            "sections": [{"name": "A", "page_start": 0, "page_end": 4}],
            "cross_page_tables": [
                {"page_start": 2, "page_end": 50, "description": "Big table"}
            ],
        }
        ds = _build_doc_structure(data, total_pages=5)
        assert ds.cross_page_tables[0].page_end == 4


# ---------------------------------------------------------------------------
# _parse_structure
# ---------------------------------------------------------------------------


class TestParseStructure:
    def test_valid_json(self):
        raw = json.dumps(SAMPLE_STRUCTURE_JSON)
        ds = _parse_structure(raw, total_pages=15)
        assert ds.document_type == "well_completion_report"
        assert len(ds.sections) == 4

    def test_json_with_fences(self):
        raw = "```json\n" + json.dumps(SAMPLE_STRUCTURE_JSON) + "\n```"
        ds = _parse_structure(raw, total_pages=15)
        assert ds.document_type == "well_completion_report"

    def test_invalid_json_returns_fallback(self):
        ds = _parse_structure("this is not json at all", total_pages=10)
        assert ds.document_type == "unknown"
        assert len(ds.sections) == 1
        assert ds.sections[0].page_end == 9

    def test_empty_string_returns_fallback(self):
        ds = _parse_structure("", total_pages=5)
        assert ds.document_type == "unknown"


# ---------------------------------------------------------------------------
# ScanResult
# ---------------------------------------------------------------------------


class TestScanResult:
    def test_properties(self):
        ds = DocStructure(document_type="test", title="Test Doc")
        mock_upload = MagicMock()
        mock_upload.cache_name = "caches/test-123"

        result = ScanResult(doc_structure=ds, upload_result=mock_upload)
        assert result.doc_structure.document_type == "test"
        assert result.cache_name == "caches/test-123"


# ---------------------------------------------------------------------------
# StructureScanner.scan() — integration with mocked Gemini
# ---------------------------------------------------------------------------


class TestStructureScannerIntegration:
    @pytest.fixture
    def settings(self):
        return Settings(
            gemini_api_key="test-key",
            extraction_model="models/gemini-2.5-flash",
        )

    @pytest.fixture
    def mock_gemini(self):
        client = MagicMock()
        client.generate = AsyncMock(return_value=json.dumps(SAMPLE_STRUCTURE_JSON))
        client.upload_file = AsyncMock()
        client.create_cache = AsyncMock()
        return client

    @pytest.mark.asyncio
    async def test_scan_returns_doc_structure(
        self, settings, mock_gemini, sample_pdf_path
    ):
        scanner = StructureScanner(mock_gemini, settings)

        # Mock the PDFUploader
        mock_upload_result = MagicMock()
        mock_upload_result.file = MagicMock()
        mock_upload_result.cache_name = "caches/test-cache"

        with patch.object(
            scanner._uploader, "upload_and_cache", new_callable=AsyncMock
        ) as mock_upload:
            mock_upload.return_value = mock_upload_result

            result = await scanner.scan(sample_pdf_path, doc_id="test-123", total_pages=15)

        assert isinstance(result, ScanResult)
        assert result.doc_structure.document_type == "well_completion_report"
        assert result.doc_structure.title == "GSWA Harvey 1 Well Completion Report"
        assert len(result.doc_structure.sections) == 4
        assert result.doc_structure.key_entities.well_name == "GSWA Harvey 1"
        assert result.cache_name == "caches/test-cache"

    @pytest.mark.asyncio
    async def test_scan_calls_uploader_and_generate(
        self, settings, mock_gemini, sample_pdf_path
    ):
        scanner = StructureScanner(mock_gemini, settings)

        mock_upload_result = MagicMock()
        mock_upload_result.file = MagicMock(name="files/test-file")
        mock_upload_result.cache_name = "caches/test-cache"

        with patch.object(
            scanner._uploader, "upload_and_cache", new_callable=AsyncMock
        ) as mock_upload:
            mock_upload.return_value = mock_upload_result

            await scanner.scan(sample_pdf_path, doc_id="test-123", total_pages=15)

        # Verify uploader was called with correct args
        mock_upload.assert_called_once()
        call_kwargs = mock_upload.call_args
        assert call_kwargs.kwargs["pdf_path"] == sample_pdf_path
        assert call_kwargs.kwargs["doc_id"] == "test-123"
        assert call_kwargs.kwargs["model"] == "models/gemini-2.5-flash"

        # Verify Gemini generate was called
        mock_gemini.generate.assert_called_once()
        gen_kwargs = mock_gemini.generate.call_args
        assert gen_kwargs.kwargs["response_mime_type"] == "application/json"
        assert gen_kwargs.kwargs["temperature"] == 0.0

    @pytest.mark.asyncio
    async def test_scan_handles_invalid_gemini_response(
        self, settings, sample_pdf_path
    ):
        """If Gemini returns garbage, we get a fallback DocStructure."""
        mock_gemini = MagicMock()
        mock_gemini.generate = AsyncMock(return_value="NOT VALID JSON {{{")

        scanner = StructureScanner(mock_gemini, settings)

        mock_upload_result = MagicMock()
        mock_upload_result.file = MagicMock()
        mock_upload_result.cache_name = "caches/test-cache"

        with patch.object(
            scanner._uploader, "upload_and_cache", new_callable=AsyncMock
        ) as mock_upload:
            mock_upload.return_value = mock_upload_result

            result = await scanner.scan(sample_pdf_path, doc_id="test-123", total_pages=10)

        # Should return fallback, not raise
        assert result.doc_structure.document_type == "unknown"
        assert len(result.doc_structure.sections) == 1
        assert result.doc_structure.sections[0].page_end == 9


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------


class TestConstants:
    def test_cache_system_instruction_not_empty(self):
        assert len(CACHE_SYSTEM_INSTRUCTION) > 50

    def test_structure_scan_prompt_mentions_json(self):
        assert "JSON" in STRUCTURE_SCAN_PROMPT or "json" in STRUCTURE_SCAN_PROMPT

    def test_structure_scan_prompt_mentions_sections(self):
        assert "sections" in STRUCTURE_SCAN_PROMPT

    def test_structure_scan_prompt_mentions_cross_page_tables(self):
        assert "cross_page_tables" in STRUCTURE_SCAN_PROMPT
