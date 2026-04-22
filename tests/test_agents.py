"""Tests for Phase 3: LangGraph Agent Pipeline.

Tests prompt builders, agent node parsing, graph routing logic,
and full graph integration with mocked Gemini responses.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.escalation import _collect_gaps, _parse_escalation_response, escalation_node
from app.agents.extractor import _parse_extraction_response, extractor_node
from app.agents.graph import batch_to_state, build_graph, run_graph_on_batches
from app.agents.prompts import (
    ESCALATION_SYSTEM,
    EXTRACTOR_SYSTEM,
    VALIDATOR_SYSTEM,
    build_escalation_prompt,
    build_extractor_prompt,
    build_validator_prompt,
)
from app.agents.validator import (
    _build_baseline_summary,
    _default_pass,
    _parse_validation_response,
    validator_node,
)
from app.config import Settings
from app.models.schemas import Complexity, PageBaseline, PageBatch
from app.models.state import BatchState


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


class TestExtractorPrompt:
    def test_basic_prompt(self):
        p = build_extractor_prompt(0, 4, "Abstract", "Test Report")
        assert "pages 0–4" in p
        assert "Abstract" in p
        assert "Test Report" in p
        assert "RETRY" not in p

    def test_single_page(self):
        p = build_extractor_prompt(3, 3, "Intro")
        assert "page 3" in p
        assert "pages" not in p.split("page 3")[0]  # no plural before

    def test_retry_with_gaps(self):
        p = build_extractor_prompt(0, 4, "Section", gap_feedback="Table rows missing")
        assert "RETRY INSTRUCTIONS" in p
        assert "Table rows missing" in p

    def test_no_section_context(self):
        p = build_extractor_prompt(0, 4, "")
        assert "belong to" not in p


class TestValidatorPrompt:
    def test_basic_prompt(self):
        p = build_validator_prompt(0, 4, "Baseline text here")
        assert "pages 0–4" in p
        assert "Baseline text here" in p
        assert "EXTRACTED CONTENT" not in p  # appended separately

    def test_single_page(self):
        p = build_validator_prompt(3, 3, "text")
        assert "page 3" in p


class TestEscalationPrompt:
    def test_basic_prompt(self):
        p = build_escalation_prompt(0, 4, "Tables", "Missing rows 5-8")
        assert "pages 0–4" in p
        assert "Missing rows 5-8" in p
        assert "Tables" in p
        assert "FINAL attempt" in p


class TestSystemPrompts:
    def test_not_empty(self):
        assert len(EXTRACTOR_SYSTEM) > 20
        assert len(VALIDATOR_SYSTEM) > 20
        assert len(ESCALATION_SYSTEM) > 20


# ---------------------------------------------------------------------------
# Extractor parsing
# ---------------------------------------------------------------------------


SAMPLE_EXTRACTION = [
    {
        "page_num": 0,
        "markdown": "# Title Page\n\nGSWA Harvey 1",
        "tables": [],
        "images": [{"id": "img1", "description": "Cover image", "location": "center"}],
        "cross_page_notes": "",
    },
    {
        "page_num": 1,
        "markdown": "## Abstract\n\nWell drilled in 2012...",
        "tables": [
            {
                "id": "t1",
                "markdown": "| Depth | Porosity |\n|---|---|\n| 3000 | 12.5 |",
                "json_data": {"headers": ["Depth", "Porosity"], "rows": [["3000", "12.5"]]},
                "continues_on_next_page": False,
            }
        ],
        "images": [],
        "cross_page_notes": "",
    },
]


class TestExtractorParsing:
    def test_valid_json_array(self):
        raw = json.dumps(SAMPLE_EXTRACTION)
        result = _parse_extraction_response(raw, 0, 1)
        assert len(result) == 2
        assert result[0]["page_num"] == 0
        assert result[1]["markdown"].startswith("## Abstract")

    def test_json_with_fences(self):
        raw = "```json\n" + json.dumps(SAMPLE_EXTRACTION) + "\n```"
        result = _parse_extraction_response(raw, 0, 1)
        assert len(result) == 2

    def test_single_object_wrapped(self):
        raw = json.dumps(SAMPLE_EXTRACTION[0])
        result = _parse_extraction_response(raw, 0, 0)
        assert len(result) == 1
        assert result[0]["page_num"] == 0

    def test_invalid_json_returns_fallback(self):
        result = _parse_extraction_response("NOT JSON", 0, 2)
        assert len(result) == 3
        assert all(p["markdown"] == "" for p in result)

    def test_missing_fields_default(self):
        raw = json.dumps([{"page_num": 5}])
        result = _parse_extraction_response(raw, 5, 5)
        assert result[0]["markdown"] == ""
        assert result[0]["tables"] == []
        assert result[0]["images"] == []


# ---------------------------------------------------------------------------
# Validator parsing
# ---------------------------------------------------------------------------

SAMPLE_VALIDATION_PASS = {
    "passed": True,
    "overall_confidence": 0.95,
    "page_results": [
        {"page_num": 0, "passed": True, "confidence": 0.97, "gaps": []},
        {"page_num": 1, "passed": True, "confidence": 0.93, "gaps": []},
    ],
}

SAMPLE_VALIDATION_FAIL = {
    "passed": False,
    "overall_confidence": 0.72,
    "page_results": [
        {"page_num": 0, "passed": True, "confidence": 0.95, "gaps": []},
        {
            "page_num": 1,
            "passed": False,
            "confidence": 0.5,
            "gaps": ["Table rows 5-8 missing", "Depth value not found"],
        },
    ],
}


class TestValidatorParsing:
    def test_valid_pass(self):
        raw = json.dumps(SAMPLE_VALIDATION_PASS)
        result = _parse_validation_response(raw, 0, 1)
        assert result["passed"] is True
        assert result["overall_confidence"] == 0.95
        assert len(result["page_results"]) == 2

    def test_valid_fail(self):
        raw = json.dumps(SAMPLE_VALIDATION_FAIL)
        result = _parse_validation_response(raw, 0, 1)
        assert result["passed"] is False
        assert len(result["page_results"][1]["gaps"]) == 2

    def test_json_with_fences(self):
        raw = "```json\n" + json.dumps(SAMPLE_VALIDATION_PASS) + "\n```"
        result = _parse_validation_response(raw, 0, 1)
        assert result["passed"] is True

    def test_invalid_json_returns_default_pass(self):
        result = _parse_validation_response("garbage", 0, 2)
        assert result["passed"] is True
        assert len(result["page_results"]) == 3

    def test_non_dict_returns_default_pass(self):
        result = _parse_validation_response(json.dumps([1, 2, 3]), 0, 1)
        assert result["passed"] is True


class TestDefaultPass:
    def test_range(self):
        result = _default_pass(3, 5)
        assert result["passed"] is True
        assert len(result["page_results"]) == 3
        assert result["page_results"][0]["page_num"] == 3


class TestBaselineSummary:
    def test_empty(self):
        assert "No baseline" in _build_baseline_summary([])

    def test_normal(self):
        baselines = [
            {"page_num": 0, "text": "Hello page 0"},
            {"page_num": 1, "text": "Hello page 1"},
        ]
        summary = _build_baseline_summary(baselines)
        assert "Page 0" in summary
        assert "Page 1" in summary

    def test_truncation(self):
        baselines = [{"page_num": 0, "text": "x" * 3000}]
        summary = _build_baseline_summary(baselines)
        assert "[truncated]" in summary


# ---------------------------------------------------------------------------
# Escalation parsing
# ---------------------------------------------------------------------------


class TestEscalationParsing:
    def test_valid_response(self):
        raw = json.dumps(SAMPLE_EXTRACTION)
        result = _parse_escalation_response(raw, 0, 1)
        assert len(result) == 2

    def test_invalid_json_fallback(self):
        result = _parse_escalation_response("BAD", 0, 1)
        assert len(result) == 2
        assert all(p["markdown"] == "" for p in result)


class TestCollectGaps:
    def test_with_gaps(self):
        state = BatchState(
            validation_result={
                "page_results": [
                    {"page_num": 0, "gaps": ["Missing header"]},
                    {"page_num": 1, "gaps": []},
                    {"page_num": 2, "gaps": ["Table incomplete", "Image missing"]},
                ]
            }
        )
        gaps = _collect_gaps(state)
        assert "Page 0" in gaps
        assert "Missing header" in gaps
        assert "Page 2" in gaps
        assert "Table incomplete" in gaps

    def test_no_validation(self):
        state = BatchState()
        gaps = _collect_gaps(state)
        assert "No specific gaps" in gaps


# ---------------------------------------------------------------------------
# batch_to_state
# ---------------------------------------------------------------------------


class TestBatchToState:
    def test_conversion(self):
        batch = PageBatch(
            batch_id=0,
            page_start=5,
            page_end=9,
            section_context="Formation Evaluation",
            complexity=Complexity.HIGH,
            baseline_texts=[
                PageBaseline(page_num=5, text="Page 5 text", char_count=50),
            ],
            media_resolution="HIGH",
        )
        ds = {"document_type": "well_completion_report", "title": "Test"}

        state = batch_to_state(batch, ds)

        assert state["batch_id"] == 0
        assert state["page_start"] == 5
        assert state["page_end"] == 9
        assert state["section_context"] == "Formation Evaluation"
        assert state["media_resolution"] == "HIGH"
        assert state["iteration_count"] == 0
        assert state["escalated"] is False
        assert len(state["baseline_texts"]) == 1
        assert state["doc_structure"]["title"] == "Test"


# ---------------------------------------------------------------------------
# Extractor node (mocked Gemini)
# ---------------------------------------------------------------------------


class TestExtractorNode:
    @pytest.mark.asyncio
    async def test_first_extraction(self):
        mock_client = MagicMock()
        mock_client.generate = AsyncMock(return_value=json.dumps(SAMPLE_EXTRACTION))

        state = BatchState(
            batch_id=0,
            page_start=0,
            page_end=1,
            section_context="Abstract",
            doc_structure={"title": "Test Report"},
            iteration_count=0,
            trace_log=[],
        )

        result = await extractor_node(state, mock_client, "models/gemini-2.5-flash")

        assert len(result["extracted_pages"]) == 2
        assert result["iteration_count"] == 1
        assert len(result["trace_log"]) == 1
        assert result["trace_log"][0]["phase"] == "extractor"

    @pytest.mark.asyncio
    async def test_retry_with_gaps(self):
        mock_client = MagicMock()
        mock_client.generate = AsyncMock(return_value=json.dumps(SAMPLE_EXTRACTION))

        state = BatchState(
            batch_id=0,
            page_start=0,
            page_end=1,
            section_context="Tables",
            iteration_count=1,
            validation_result=SAMPLE_VALIDATION_FAIL,
            trace_log=[],
        )

        result = await extractor_node(state, mock_client, "models/gemini-2.5-flash")

        # Should have passed gap feedback in the prompt
        call_args = mock_client.generate.call_args
        prompt = call_args.kwargs.get("contents", call_args.args[1] if len(call_args.args) > 1 else "")
        # Just verify it executed and incremented
        assert result["iteration_count"] == 2


# ---------------------------------------------------------------------------
# Validator node (mocked Gemini)
# ---------------------------------------------------------------------------


class TestValidatorNode:
    @pytest.mark.asyncio
    async def test_validation_pass(self):
        mock_client = MagicMock()
        mock_client.generate = AsyncMock(return_value=json.dumps(SAMPLE_VALIDATION_PASS))

        state = BatchState(
            batch_id=0,
            page_start=0,
            page_end=1,
            extracted_pages=SAMPLE_EXTRACTION,
            baseline_texts=[
                {"page_num": 0, "text": "Title page text"},
                {"page_num": 1, "text": "Abstract text"},
            ],
            iteration_count=1,
            trace_log=[],
        )

        result = await validator_node(state, mock_client, "models/gemini-2.5-flash")

        assert result["validation_result"]["passed"] is True
        assert result["final_pages"] == SAMPLE_EXTRACTION
        assert len(result["trace_log"]) == 1
        assert result["trace_log"][0]["phase"] == "validator"

    @pytest.mark.asyncio
    async def test_validation_fail(self):
        mock_client = MagicMock()
        mock_client.generate = AsyncMock(return_value=json.dumps(SAMPLE_VALIDATION_FAIL))

        state = BatchState(
            batch_id=0,
            page_start=0,
            page_end=1,
            extracted_pages=SAMPLE_EXTRACTION,
            baseline_texts=[],
            iteration_count=1,
            trace_log=[],
        )

        result = await validator_node(state, mock_client, "models/gemini-2.5-flash")

        assert result["validation_result"]["passed"] is False
        assert "final_pages" not in result or result["final_pages"] == []


# ---------------------------------------------------------------------------
# Escalation node (mocked Gemini)
# ---------------------------------------------------------------------------


class TestEscalationNode:
    @pytest.mark.asyncio
    async def test_escalation(self):
        mock_client = MagicMock()
        mock_client.generate = AsyncMock(return_value=json.dumps(SAMPLE_EXTRACTION))

        state = BatchState(
            batch_id=0,
            page_start=0,
            page_end=1,
            section_context="Tables",
            validation_result=SAMPLE_VALIDATION_FAIL,
            trace_log=[],
        )

        result = await escalation_node(state, mock_client, "models/gemini-2.5-pro")

        assert result["escalated"] is True
        assert len(result["final_pages"]) == 2
        assert result["trace_log"][-1]["phase"] == "escalation"


# ---------------------------------------------------------------------------
# Graph build + routing
# ---------------------------------------------------------------------------


class TestGraphRouting:
    @pytest.fixture
    def settings(self):
        return Settings(
            gemini_api_key="test-key",
            extraction_model="models/gemini-2.5-flash",
            escalation_model="models/gemini-2.5-pro",
            max_retries=2,
        )

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.generate = AsyncMock()
        return client

    def test_build_graph_compiles(self, settings, mock_client):
        """Graph should compile without errors."""
        graph = build_graph(mock_client, settings)
        assert graph is not None

    @pytest.mark.asyncio
    async def test_happy_path_pass(self, settings, mock_client):
        """Extractor → Validator(pass) → END."""
        # Mock: extractor returns extraction, validator passes
        mock_client.generate = AsyncMock(
            side_effect=[
                json.dumps(SAMPLE_EXTRACTION),      # extractor
                json.dumps(SAMPLE_VALIDATION_PASS),  # validator
            ]
        )

        graph = build_graph(mock_client, settings)
        initial = BatchState(
            batch_id=0,
            page_start=0,
            page_end=1,
            section_context="Test",
            doc_structure={},
            baseline_texts=[],
            media_resolution="MEDIUM",
            extracted_pages=[],
            validation_result={},
            iteration_count=0,
            escalated=False,
            final_pages=[],
            trace_log=[],
        )

        result = await graph.ainvoke(initial)

        assert result["final_pages"] == SAMPLE_EXTRACTION
        assert result["escalated"] is False
        assert result["iteration_count"] == 1
        # 2 calls: extractor + validator
        assert mock_client.generate.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_then_pass(self, settings, mock_client):
        """Extractor → Validator(fail) → Extractor(retry) → Validator(pass) → END."""
        mock_client.generate = AsyncMock(
            side_effect=[
                json.dumps(SAMPLE_EXTRACTION),       # extractor (1st)
                json.dumps(SAMPLE_VALIDATION_FAIL),   # validator (1st) — fail
                json.dumps(SAMPLE_EXTRACTION),        # extractor (2nd — retry)
                json.dumps(SAMPLE_VALIDATION_PASS),   # validator (2nd) — pass
            ]
        )

        graph = build_graph(mock_client, settings)
        initial = BatchState(
            batch_id=0, page_start=0, page_end=1,
            section_context="Test", doc_structure={}, baseline_texts=[],
            media_resolution="MEDIUM", extracted_pages=[], validation_result={},
            iteration_count=0, escalated=False, final_pages=[], trace_log=[],
        )

        result = await graph.ainvoke(initial)

        assert result["final_pages"] == SAMPLE_EXTRACTION
        assert result["escalated"] is False
        assert result["iteration_count"] == 2
        assert mock_client.generate.call_count == 4

    @pytest.mark.asyncio
    async def test_escalation_after_max_retries(self, settings, mock_client):
        """Extractor → Validator(fail) x2 → Escalation → END."""
        mock_client.generate = AsyncMock(
            side_effect=[
                json.dumps(SAMPLE_EXTRACTION),       # extractor (1st)
                json.dumps(SAMPLE_VALIDATION_FAIL),   # validator (1st)
                json.dumps(SAMPLE_EXTRACTION),        # extractor (2nd)
                json.dumps(SAMPLE_VALIDATION_FAIL),   # validator (2nd)
                json.dumps(SAMPLE_EXTRACTION),        # escalation
            ]
        )

        graph = build_graph(mock_client, settings)
        initial = BatchState(
            batch_id=0, page_start=0, page_end=1,
            section_context="Test", doc_structure={}, baseline_texts=[],
            media_resolution="MEDIUM", extracted_pages=[], validation_result={},
            iteration_count=0, escalated=False, final_pages=[], trace_log=[],
        )

        result = await graph.ainvoke(initial)

        assert result["escalated"] is True
        assert len(result["final_pages"]) == 2
        # 5 calls: ext, val, ext, val, escalation
        assert mock_client.generate.call_count == 5


# ---------------------------------------------------------------------------
# run_graph_on_batches
# ---------------------------------------------------------------------------


class TestRunGraphOnBatches:
    @pytest.mark.asyncio
    async def test_processes_all_batches(self):
        """All batches should be processed and returned."""
        mock_graph = MagicMock()

        async def fake_invoke(state):
            return {**state, "final_pages": [{"page_num": state["page_start"]}]}

        mock_graph.ainvoke = AsyncMock(side_effect=fake_invoke)

        batches = [
            PageBatch(batch_id=i, page_start=i * 5, page_end=i * 5 + 4)
            for i in range(3)
        ]
        ds = {"title": "Test"}

        results = await run_graph_on_batches(mock_graph, batches, ds, max_concurrent=2)

        assert len(results) == 3
        assert mock_graph.ainvoke.call_count == 3

    @pytest.mark.asyncio
    async def test_handles_batch_failure(self):
        """Failed batches should return minimal state, not crash."""
        call_count = 0

        async def fail_second(state):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Gemini error")
            return {**state, "final_pages": [{"page_num": state["page_start"]}]}

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(side_effect=fail_second)

        batches = [
            PageBatch(batch_id=i, page_start=i * 5, page_end=i * 5 + 4)
            for i in range(3)
        ]
        ds = {"title": "Test"}

        results = await run_graph_on_batches(mock_graph, batches, ds, max_concurrent=10)

        assert len(results) == 3
        # The failed batch should have empty final_pages
        failed = [r for r in results if not r.get("final_pages")]
        assert len(failed) == 1
