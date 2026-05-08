"""Tests for the LLM artifact-class auto-classifier."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.pipeline.artifact_classifier import (
    _format_outline_for_prompt,
    _parse_response,
    classify_artifact,
)


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def test_format_outline_includes_filename_and_headings():
    out = _format_outline_for_prompt(
        filename="ALP-12_DDR_2026-05-08.pdf",
        headings=[
            {"page": 1, "text": "Daily Drilling Report"},
            {"page": 2, "text": "Operations Summary"},
        ],
        page_count=5,
        table_count=3,
        image_count=1,
        content_sample="Drilling at 3450 ft today, mud weight 11.2 ppg.",
    )
    assert "ALP-12_DDR_2026-05-08.pdf" in out
    assert "page_count: 5" in out
    assert "p.1: Daily Drilling Report" in out
    assert "p.2: Operations Summary" in out
    assert "Drilling at 3450 ft" in out


def test_format_outline_handles_missing_pages():
    out = _format_outline_for_prompt(
        filename="x.pdf",
        headings=[{"page": None, "text": "Heading"}],
        page_count=None,
        table_count=0,
        image_count=0,
        content_sample="",
    )
    assert "page_count: unknown" in out
    assert "  Heading" in out
    assert "(empty)" in out


def test_format_outline_truncates_content_sample():
    sample = "x" * 5000
    out = _format_outline_for_prompt(
        filename="x.pdf",
        headings=[],
        page_count=1,
        table_count=0,
        image_count=0,
        content_sample=sample,
    )
    # 600 chars + ellipsis + space
    assert "x" * 600 in out
    assert "x" * 700 not in out


def test_format_outline_caps_headings():
    headings = [{"page": i, "text": f"H{i}"} for i in range(50)]
    out = _format_outline_for_prompt(
        filename="x.pdf",
        headings=headings,
        page_count=50,
        table_count=0,
        image_count=0,
        content_sample="",
    )
    # Only first 10 included
    assert "p.9: H9" in out
    assert "p.10: H10" not in out


def test_format_outline_no_headings_block():
    out = _format_outline_for_prompt(
        filename="x.pdf",
        headings=[],
        page_count=1,
        table_count=0,
        image_count=0,
        content_sample="hi",
    )
    assert "(no headings parsed)" in out


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------


def test_parse_clean_response():
    cls, conf = _parse_response('{"artifact_class": "ddr", "confidence": 0.92}')
    assert cls == "ddr"
    assert conf == 0.92


def test_parse_lowercases_class():
    cls, _ = _parse_response('{"artifact_class": "DDR", "confidence": 0.9}')
    assert cls == "ddr"


def test_parse_strips_code_fence():
    raw = '```json\n{"artifact_class": "program", "confidence": 0.8}\n```'
    cls, conf = _parse_response(raw)
    assert cls == "program"
    assert conf == 0.8


def test_parse_unknown_class_falls_back_to_other():
    cls, conf = _parse_response('{"artifact_class": "invoice", "confidence": 0.9}')
    assert cls == "other"
    assert conf == 0.0


def test_parse_invalid_json_falls_back():
    cls, conf = _parse_response("not json")
    assert cls == "other"
    assert conf == 0.0


def test_parse_clamps_confidence():
    cls, conf = _parse_response('{"artifact_class": "ddr", "confidence": 5.0}')
    assert cls == "ddr"
    assert conf == 1.0


def test_parse_handles_missing_confidence():
    cls, conf = _parse_response('{"artifact_class": "ddr"}')
    assert cls == "ddr"
    assert conf == 0.0


def test_parse_handles_non_dict():
    cls, conf = _parse_response("[1, 2, 3]")
    assert cls == "other"
    assert conf == 0.0


# ---------------------------------------------------------------------------
# classify_artifact (full call with stubbed Gemini)
# ---------------------------------------------------------------------------


def _stub_client(text: str):
    """Build a fake genai.Client whose generate_content returns ``text``."""
    response = SimpleNamespace(text=text)
    aio_models = SimpleNamespace(generate_content=AsyncMock(return_value=response))
    aio = SimpleNamespace(models=aio_models)
    return SimpleNamespace(aio=aio)


@pytest.mark.asyncio
async def test_classify_returns_class_from_llm():
    client = _stub_client('{"artifact_class": "program", "confidence": 0.91}')
    settings = Settings(gemini_api_key="x")
    cls, conf = await classify_artifact(
        filename="WellPlan_v3.pdf",
        headings=[{"page": 1, "text": "Drilling Program"}],
        page_count=10,
        table_count=2,
        image_count=1,
        content_sample="Approved drilling program for Well ALP-12.",
        settings=settings,
        client=client,
    )
    assert cls == "program"
    assert conf == 0.91
    client.aio.models.generate_content.assert_awaited_once()


@pytest.mark.asyncio
async def test_classify_returns_other_on_llm_failure():
    aio_models = SimpleNamespace(
        generate_content=AsyncMock(side_effect=RuntimeError("boom"))
    )
    aio = SimpleNamespace(models=aio_models)
    client = SimpleNamespace(aio=aio)

    settings = Settings(gemini_api_key="x")
    cls, conf = await classify_artifact(
        filename="x.pdf",
        headings=[],
        page_count=1,
        table_count=0,
        image_count=0,
        content_sample="",
        settings=settings,
        client=client,
    )
    assert cls == "other"
    assert conf == 0.0


@pytest.mark.asyncio
async def test_classify_returns_other_when_llm_invents_class():
    client = _stub_client('{"artifact_class": "spaceship", "confidence": 0.9}')
    settings = Settings(gemini_api_key="x")
    cls, conf = await classify_artifact(
        filename="x.pdf",
        headings=[],
        page_count=1,
        table_count=0,
        image_count=0,
        content_sample="",
        settings=settings,
        client=client,
    )
    assert cls == "other"
    assert conf == 0.0


@pytest.mark.asyncio
async def test_classify_returns_other_on_garbage_response():
    client = _stub_client("hello world")
    settings = Settings(gemini_api_key="x")
    cls, _ = await classify_artifact(
        filename="x.pdf",
        headings=[],
        page_count=1,
        table_count=0,
        image_count=0,
        content_sample="",
        settings=settings,
        client=client,
    )
    assert cls == "other"
