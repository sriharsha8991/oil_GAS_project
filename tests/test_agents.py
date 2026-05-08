"""Tests for the per-page extraction pipeline (extractor + escalation + graph).

The pipeline is now a 2-stage flow: extractor (Flash, self-validating) →
END | escalation (Pro, FINAL). There is no separate validator stage and
no multi-iteration retry loop — see ``app/agents/graph.py``.
"""

from __future__ import annotations

import pytest

from app.agents.escalation import _parse_extraction as _escalation_parse
from app.agents.extractor import _parse_extraction as _extractor_parse
from app.agents.graph import build_graph, page_image_to_state
from app.agents.prompts import (
    ESCALATION_SYSTEM,
    EXTRACTOR_SYSTEM,
    build_escalation_prompt,
    build_extractor_prompt,
)
from app.config import Settings
from app.models.schemas import PageImage


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


class TestExtractorPrompt:
    def test_basic_prompt_has_page_num(self):
        p = build_extractor_prompt(page_num=5)
        assert "page 5" in p

    def test_scanned_hint_added(self):
        p = build_extractor_prompt(page_num=0, is_scanned=True)
        assert "scanned" in p.lower()

    def test_baseline_hint_for_text_page(self):
        p = build_extractor_prompt(
            page_num=0, is_scanned=False, baseline_text="ABC well report"
        )
        assert "ABC well report" in p

    def test_no_baseline_hint_for_scanned(self):
        p = build_extractor_prompt(
            page_num=0, is_scanned=True, baseline_text="some text"
        )
        assert "some text" not in p


class TestEscalationPrompt:
    def test_basic(self):
        p = build_escalation_prompt(page_num=7, previous_gaps="- missing table row")
        assert "page 7" in p
        assert "missing table row" in p


class TestSystemPrompts:
    def test_all_present(self):
        assert EXTRACTOR_SYSTEM
        assert ESCALATION_SYSTEM


# ---------------------------------------------------------------------------
# JSON parsers
# ---------------------------------------------------------------------------


class TestExtractorParser:
    def test_clean_json_passes(self):
        raw = (
            '{"page_num": 3, "markdown": "hi", "tables": [], "images": [], '
            '"entities": [], "self_confidence": 0.95, "self_gaps": []}'
        )
        extracted, validation = _extractor_parse(raw, page_num=3, pass_threshold=0.85)
        assert extracted["page_num"] == 3
        assert extracted["markdown"] == "hi"
        assert validation["passed"] is True
        assert validation["confidence"] == 0.95

    def test_low_confidence_fails(self):
        raw = '{"page_num": 1, "markdown": "x", "self_confidence": 0.5, "self_gaps": []}'
        _, validation = _extractor_parse(raw, page_num=1, pass_threshold=0.85)
        assert validation["passed"] is False

    def test_gaps_force_fail(self):
        raw = '{"page_num": 1, "markdown": "x", "self_confidence": 0.95, "self_gaps": ["miss"]}'
        _, validation = _extractor_parse(raw, page_num=1, pass_threshold=0.85)
        assert validation["passed"] is False
        assert "miss" in validation["gaps"]

    def test_json_in_code_fence(self):
        raw = '```json\n{"page_num": 1, "markdown": "x", "self_confidence": 0.9}\n```'
        extracted, _ = _extractor_parse(raw, page_num=1, pass_threshold=0.85)
        assert extracted["markdown"] == "x"

    def test_invalid_json_forces_escalation(self):
        extracted, validation = _extractor_parse(
            "not json", page_num=4, pass_threshold=0.85
        )
        assert extracted["page_num"] == 4
        assert extracted["markdown"] == ""
        assert validation["passed"] is False


class TestEscalationParser:
    def test_basic(self):
        raw = '{"page_num": 5, "markdown": "Pro extracted"}'
        out = _escalation_parse(raw, page_num=5)
        assert out["markdown"] == "Pro extracted"


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
        from unittest.mock import MagicMock

        client = MagicMock()
        settings = Settings(gemini_api_key="dummy")
        graph = build_graph(client, settings)
        assert graph is not None


class TestGraphRouting:
    """Drive the graph with a stub client through both terminal paths."""

    @pytest.mark.asyncio
    async def test_pass_after_extractor(self):
        from unittest.mock import AsyncMock

        client = AsyncMock()
        client.generate_with_image = AsyncMock(
            return_value=(
                '{"page_num": 0, "markdown": "extracted", '
                '"self_confidence": 0.95, "self_gaps": []}',
                {"input_tokens": 1, "output_tokens": 1},
            )
        )
        settings = Settings(gemini_api_key="x")
        graph = build_graph(client, settings)
        result = await graph.ainvoke(
            page_image_to_state(PageImage(page_num=0, png_bytes=b"png"))
        )
        assert result["escalated"] is False
        assert result["extracted"]["markdown"] == "extracted"
        assert client.generate_with_image.await_count == 1

    @pytest.mark.asyncio
    async def test_escalate_to_pro_on_low_confidence(self):
        from unittest.mock import AsyncMock

        client = AsyncMock()
        client.generate_with_image = AsyncMock(
            side_effect=[
                (
                    '{"page_num": 0, "markdown": "v1", '
                    '"self_confidence": 0.4, "self_gaps": ["miss"]}',
                    {"input_tokens": 1, "output_tokens": 1},
                ),
                (
                    '{"page_num": 0, "markdown": "pro_final"}',
                    {"input_tokens": 1, "output_tokens": 1},
                ),
            ]
        )
        settings = Settings(gemini_api_key="x")
        graph = build_graph(client, settings)
        result = await graph.ainvoke(
            page_image_to_state(PageImage(page_num=0, png_bytes=b"p"))
        )
        assert result["escalated"] is True
        assert result["final"]["markdown"] == "pro_final"
        assert client.generate_with_image.await_count == 2

    @pytest.mark.asyncio
    async def test_pro_uses_escalation_model(self):
        from unittest.mock import AsyncMock

        client = AsyncMock()
        client.generate_with_image = AsyncMock(
            side_effect=[
                (
                    '{"page_num": 0, "markdown": "v1", '
                    '"self_confidence": 0.4, "self_gaps": ["g"]}',
                    {"input_tokens": 1, "output_tokens": 1},
                ),
                (
                    '{"page_num": 0, "markdown": "pro"}',
                    {"input_tokens": 1, "output_tokens": 1},
                ),
            ]
        )
        settings = Settings(
            gemini_api_key="x",
            extraction_model="models/gemini-3-flash-preview",
            escalation_model="models/gemini-3.1-pro-preview",
        )
        graph = build_graph(client, settings)
        await graph.ainvoke(
            page_image_to_state(PageImage(page_num=0, png_bytes=b"p"))
        )
        models_used = [
            call.kwargs["model"] for call in client.generate_with_image.await_args_list
        ]
        assert models_used[0] == "models/gemini-3-flash-preview"
        assert models_used[-1] == "models/gemini-3.1-pro-preview"
