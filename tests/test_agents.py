"""Tests for the v3 per-page agent pipeline (extractor, validator, escalation, graph)."""

from __future__ import annotations

import pytest

from app.agents.escalation import _collect_gaps, _parse_extraction
from app.agents.extractor import _parse_extraction as _extractor_parse
from app.agents.graph import build_graph, page_image_to_state
from app.agents.prompts import (
    ESCALATION_SYSTEM,
    EXTRACTOR_SYSTEM,
    VALIDATOR_SYSTEM,
    build_escalation_prompt,
    build_extractor_prompt,
    build_validator_prompt,
)
from app.agents.validator import _parse_validation
from app.config import Settings
from app.models.schemas import PageImage
from app.models.state import PageState


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


class TestExtractorPrompt:
    def test_basic_prompt_has_page_num(self):
        p = build_extractor_prompt(page_num=5)
        assert "page 5" in p
        assert "JSON object" in p

    def test_scanned_hint_added(self):
        p = build_extractor_prompt(page_num=0, is_scanned=True)
        assert "scanned" in p.lower()

    def test_baseline_hint_for_text_page(self):
        p = build_extractor_prompt(
            page_num=0, is_scanned=False, baseline_text="ABC well report"
        )
        assert "ABC well report" in p
        assert "HINT" in p

    def test_baseline_truncated_when_long(self):
        long_text = "x" * 5000
        p = build_extractor_prompt(page_num=0, baseline_text=long_text)
        assert "truncated" in p

    def test_no_baseline_hint_for_scanned(self):
        p = build_extractor_prompt(
            page_num=0, is_scanned=True, baseline_text="some text"
        )
        assert "some text" not in p

    def test_gap_feedback_block_on_retry(self):
        p = build_extractor_prompt(
            page_num=0, gap_feedback="- missing depth value 3450ft"
        )
        assert "RETRY" in p
        assert "3450ft" in p


class TestValidatorPrompt:
    def test_includes_extracted_and_baseline(self):
        p = build_validator_prompt(
            page_num=2,
            extracted_json='{"page_num":2,"markdown":"hello"}',
            baseline_text="baseline text here",
        )
        assert "page 2" in p
        assert "baseline text here" in p
        assert "hello" in p

    def test_pass_threshold_referenced(self):
        p = build_validator_prompt(
            page_num=0, extracted_json="{}", baseline_text="", pass_threshold=0.9
        )
        assert "0.9" in p


class TestEscalationPrompt:
    def test_basic(self):
        p = build_escalation_prompt(page_num=7, previous_gaps="- missing table row")
        assert "page 7" in p
        assert "missing table row" in p
        assert "FINAL" in p


class TestSystemPrompts:
    def test_all_present(self):
        assert EXTRACTOR_SYSTEM
        assert VALIDATOR_SYSTEM
        assert ESCALATION_SYSTEM


# ---------------------------------------------------------------------------
# JSON parsers
# ---------------------------------------------------------------------------


class TestExtractorParser:
    def test_clean_json(self):
        raw = '{"page_num": 3, "markdown": "hi", "tables": [], "images": [], "cross_page_notes": ""}'
        out = _extractor_parse(raw, page_num=3)
        assert out["page_num"] == 3
        assert out["markdown"] == "hi"

    def test_json_in_code_fence(self):
        raw = '```json\n{"page_num": 1, "markdown": "x"}\n```'
        out = _extractor_parse(raw, page_num=1)
        assert out["markdown"] == "x"

    def test_unwrap_single_element_list(self):
        raw = '[{"page_num": 0, "markdown": "first"}]'
        out = _extractor_parse(raw, page_num=0)
        assert out["markdown"] == "first"

    def test_invalid_json_returns_empty(self):
        out = _extractor_parse("not json at all", page_num=4)
        assert out["page_num"] == 4
        assert out["markdown"] == ""
        assert out["tables"] == []


class TestValidatorParser:
    def test_pass_meets_threshold_no_gaps(self):
        raw = '{"page_num": 2, "passed": true, "confidence": 0.9, "gaps": []}'
        v = _parse_validation(raw, page_num=2, pass_threshold=0.85)
        assert v["passed"] is True
        assert v["confidence"] == 0.9

    def test_fail_below_threshold(self):
        raw = '{"page_num": 2, "passed": true, "confidence": 0.7, "gaps": []}'
        v = _parse_validation(raw, page_num=2, pass_threshold=0.85)
        assert v["passed"] is False  # threshold overrides

    def test_fail_with_gaps(self):
        raw = '{"page_num": 2, "passed": true, "confidence": 0.95, "gaps": ["missing"]}'
        v = _parse_validation(raw, page_num=2, pass_threshold=0.85)
        assert v["passed"] is False  # gaps override

    def test_invalid_json_defaults_pass(self):
        v = _parse_validation("garbage", page_num=1, pass_threshold=0.85)
        assert v["passed"] is True  # safety: don't loop forever on parse errors


class TestEscalationParser:
    def test_basic(self):
        raw = '{"page_num": 5, "markdown": "Pro extracted"}'
        out = _parse_extraction(raw, page_num=5)
        assert out["markdown"] == "Pro extracted"

    def test_collect_gaps_with_validation(self):
        state: PageState = {
            "page_num": 1,
            "validation": {"gaps": ["g1", "g2"]},
        }
        result = _collect_gaps(state)
        assert "g1" in result and "g2" in result

    def test_collect_gaps_empty(self):
        state: PageState = {"page_num": 1, "validation": {"gaps": []}}
        result = _collect_gaps(state)
        assert "No specific gaps" in result


# ---------------------------------------------------------------------------
# Graph wiring
# ---------------------------------------------------------------------------


class TestPageImageToState:
    def test_basic(self):
        page = PageImage(
            page_num=4, png_bytes=b"\x89PNG", baseline_text="ref",
            is_scanned=False, dpi=200,
        )
        s = page_image_to_state(page)
        assert s["page_num"] == 4
        assert s["png_bytes"] == b"\x89PNG"
        assert s["iteration"] == 0
        assert s["escalated"] is False


class TestBuildGraph:
    def test_compiles(self):
        # Use a stub client (no real API calls — graph only compiled here)
        from unittest.mock import MagicMock

        client = MagicMock()
        settings = Settings(gemini_api_key="dummy")
        graph = build_graph(client, settings)
        assert graph is not None


class TestGraphRouting:
    """Integration: drive the graph with a stub client through all 3 paths."""

    @pytest.mark.asyncio
    async def test_pass_after_iter1(self, monkeypatch):
        from unittest.mock import AsyncMock

        client = AsyncMock()
        # Iter 1 extractor → returns extraction; validator → passes
        client.generate_with_image = AsyncMock(
            side_effect=[
                '{"page_num": 0, "markdown": "extracted"}',
                '{"page_num": 0, "passed": true, "confidence": 0.95, "gaps": []}',
            ]
        )
        settings = Settings(gemini_api_key="x", max_iterations=3)
        graph = build_graph(client, settings)

        page = PageImage(page_num=0, png_bytes=b"png", baseline_text="b")
        result = await graph.ainvoke(page_image_to_state(page))

        assert result["iteration"] == 1
        assert result["escalated"] is False
        assert result["final"]["markdown"] == "extracted"
        assert client.generate_with_image.await_count == 2

    @pytest.mark.asyncio
    async def test_pass_after_iter2_retry(self):
        from unittest.mock import AsyncMock

        client = AsyncMock()
        client.generate_with_image = AsyncMock(
            side_effect=[
                # iter 1
                '{"page_num": 0, "markdown": "v1"}',
                '{"page_num": 0, "passed": false, "confidence": 0.5, "gaps": ["miss"]}',
                # iter 2
                '{"page_num": 0, "markdown": "v2"}',
                '{"page_num": 0, "passed": true, "confidence": 0.9, "gaps": []}',
            ]
        )
        settings = Settings(gemini_api_key="x", max_iterations=3)
        graph = build_graph(client, settings)

        result = await graph.ainvoke(
            page_image_to_state(PageImage(page_num=0, png_bytes=b"p"))
        )

        assert result["iteration"] == 2
        assert result["escalated"] is False
        assert result["final"]["markdown"] == "v2"
        assert client.generate_with_image.await_count == 4

    @pytest.mark.asyncio
    async def test_escalate_to_pro_after_iter2_fail(self):
        from unittest.mock import AsyncMock

        client = AsyncMock()
        client.generate_with_image = AsyncMock(
            side_effect=[
                # iter 1
                '{"page_num": 0, "markdown": "v1"}',
                '{"page_num": 0, "passed": false, "confidence": 0.4, "gaps": ["g1"]}',
                # iter 2
                '{"page_num": 0, "markdown": "v2"}',
                '{"page_num": 0, "passed": false, "confidence": 0.5, "gaps": ["g2"]}',
                # iter 3 (Pro escalation, no validator after)
                '{"page_num": 0, "markdown": "pro_final"}',
            ]
        )
        settings = Settings(gemini_api_key="x", max_iterations=3)
        graph = build_graph(client, settings)

        result = await graph.ainvoke(
            page_image_to_state(PageImage(page_num=0, png_bytes=b"p"))
        )

        assert result["iteration"] == 3
        assert result["escalated"] is True
        assert result["final"]["markdown"] == "pro_final"
        assert client.generate_with_image.await_count == 5  # 2+2+1

    @pytest.mark.asyncio
    async def test_pro_uses_escalation_model(self):
        """Verify iter 3 is dispatched to the Pro model, not Flash."""
        from unittest.mock import AsyncMock

        client = AsyncMock()
        client.generate_with_image = AsyncMock(
            side_effect=[
                '{"page_num": 0, "markdown": "v1"}',
                '{"page_num": 0, "passed": false, "confidence": 0.4, "gaps": ["g"]}',
                '{"page_num": 0, "markdown": "v2"}',
                '{"page_num": 0, "passed": false, "confidence": 0.4, "gaps": ["g"]}',
                '{"page_num": 0, "markdown": "pro"}',
            ]
        )
        settings = Settings(
            gemini_api_key="x",
            max_iterations=3,
            extraction_model="models/gemini-3-flash-preview",
            escalation_model="models/gemini-3.1-pro-preview",
        )
        graph = build_graph(client, settings)
        await graph.ainvoke(
            page_image_to_state(PageImage(page_num=0, png_bytes=b"p"))
        )

        # The 5th call (iter 3) must use the escalation model
        models_used = [
            call.kwargs["model"] for call in client.generate_with_image.await_args_list
        ]
        assert models_used[-1] == "models/gemini-3.1-pro-preview"  # escalation
        # iter 1 & 2 (calls 0-3) should be Flash
        assert all(m == "models/gemini-3-flash-preview" for m in models_used[:4])
