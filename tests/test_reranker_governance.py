"""Tests for the tier-weighted reranker and the rag_tools governance pass."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.agents.rag_tools import _apply_governance
from app.db.queries.document_meta import DocumentMeta
from app.retrieval.reranker import (
    RankedResult,
    get_tier_weight,
    merge_and_rerank,
    rerank,
)


# ---------------------------------------------------------------------------
# Tier weight lookup
# ---------------------------------------------------------------------------


def test_tier_weight_master_and_program_are_top():
    assert get_tier_weight("master") == 1.0
    assert get_tier_weight("program") == 1.0
    assert get_tier_weight("learning") == 1.0


def test_tier_weight_tier2():
    assert get_tier_weight("ddr") == 0.85
    assert get_tier_weight("handover") == 0.85
    assert get_tier_weight("risk") == 0.85


def test_tier_weight_media_is_lowest():
    assert get_tier_weight("media") == 0.3


def test_tier_weight_case_insensitive():
    assert get_tier_weight("DDR") == 0.85
    assert get_tier_weight("Program") == 1.0


def test_tier_weight_unknown_uses_default():
    assert get_tier_weight("invoice") == 0.5
    assert get_tier_weight("") == 0.5
    assert get_tier_weight(None) == 0.5


# ---------------------------------------------------------------------------
# rerank() per-result behaviour
# ---------------------------------------------------------------------------


def test_rerank_uses_per_result_artifact_class():
    raw = [
        # high cosine but low-tier
        {"text": "media a", "score": 0.95, "artifact_class": "media"},
        # lower cosine but program-tier
        {"text": "program b", "score": 0.7, "artifact_class": "program"},
    ]
    ranked = rerank(raw)
    # 0.95 * 0.3 = 0.285 vs 0.7 * 1.0 = 0.7 → program wins
    assert ranked[0].text == "program b"
    assert ranked[0].adjusted_score == 0.7
    assert ranked[1].text == "media a"
    assert ranked[1].adjusted_score == 0.285


def test_rerank_missing_class_falls_back_to_default():
    raw = [
        {"text": "x", "score": 0.5},                               # default 0.5
        {"text": "y", "score": 0.5, "artifact_class": "program"},  # 1.0
    ]
    ranked = rerank(raw)
    assert ranked[0].text == "y"
    assert ranked[0].tier_weight == 1.0
    assert ranked[1].tier_weight == 0.5


def test_rerank_preserves_payload_fields():
    raw = [
        {
            "text": "hi",
            "score": 0.8,
            "artifact_class": "ddr",
            "confidentiality": "restricted",
            "user_id": "u",
            "document_id": "d",
            "page_num": 5,
        }
    ]
    ranked = rerank(raw, collection="wellsynthai_documents")
    r = ranked[0]
    assert r.artifact_class == "ddr"
    assert r.confidentiality == "restricted"
    assert r.user_id == "u"
    assert r.document_id == "d"
    assert r.page_num == 5
    assert r.collection == "wellsynthai_documents"


def test_rerank_empty():
    assert rerank([]) == []


def test_merge_and_rerank_sorts_across_lists():
    a = [RankedResult(text="a", raw_score=0.9, adjusted_score=0.9, tier_weight=1.0)]
    b = [RankedResult(text="b", raw_score=0.8, adjusted_score=0.95, tier_weight=1.0)]
    merged = merge_and_rerank(a, b, limit=10)
    assert merged[0].text == "b"
    assert merged[1].text == "a"


def test_merge_and_rerank_respects_limit():
    a = [
        RankedResult(text=f"r{i}", raw_score=1.0, adjusted_score=1.0 - i * 0.1,
                     tier_weight=1.0)
        for i in range(5)
    ]
    merged = merge_and_rerank(a, limit=3)
    assert len(merged) == 3


# ---------------------------------------------------------------------------
# _apply_governance — Postgres-backed enrichment + supersession filter
# ---------------------------------------------------------------------------


def _meta(cls="ddr", conf="internal", rev=1, superseded=False) -> DocumentMeta:
    return DocumentMeta(
        artifact_class=cls,
        confidentiality=conf,
        revision=rev,
        is_superseded=superseded,
    )


@pytest.mark.asyncio
async def test_apply_governance_attaches_artifact_class():
    raw = [
        {"text": "a", "score": 0.9, "document_id": "doc-1"},
        {"text": "b", "score": 0.8, "document_id": "doc-2"},
    ]
    fake = {"doc-1": _meta(cls="program"), "doc-2": _meta(cls="media")}

    with (
        patch("app.agents.rag_tools.get_session_factory") as gsf,
        patch(
            "app.agents.rag_tools.fetch_document_meta",
            return_value=fake,
        ) as fetch,
    ):
        # session factory returns a context manager that yields a stub session
        class _S:
            async def __aenter__(self):
                return "session"

            async def __aexit__(self, *a):
                return False

        gsf.return_value = lambda: _S()

        out = await _apply_governance(raw)

    assert len(out) == 2
    by_text = {r["text"]: r for r in out}
    assert by_text["a"]["artifact_class"] == "program"
    assert by_text["b"]["artifact_class"] == "media"
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_governance_drops_superseded():
    raw = [
        {"text": "old", "score": 0.95, "document_id": "doc-old"},
        {"text": "new", "score": 0.7,  "document_id": "doc-new"},
    ]
    fake = {
        "doc-old": _meta(cls="program", superseded=True),
        "doc-new": _meta(cls="program", superseded=False),
    }

    with (
        patch("app.agents.rag_tools.get_session_factory") as gsf,
        patch("app.agents.rag_tools.fetch_document_meta", return_value=fake),
    ):
        class _S:
            async def __aenter__(self): return "s"
            async def __aexit__(self, *a): return False
        gsf.return_value = lambda: _S()

        out = await _apply_governance(raw)

    assert len(out) == 1
    assert out[0]["text"] == "new"


@pytest.mark.asyncio
async def test_apply_governance_keeps_orphan_with_no_meta():
    """Vector points whose document row was deleted should still surface
    (with default tier weight) rather than disappear silently."""
    raw = [{"text": "orphan", "score": 0.9, "document_id": "doc-gone"}]

    with (
        patch("app.agents.rag_tools.get_session_factory") as gsf,
        patch("app.agents.rag_tools.fetch_document_meta", return_value={}),
    ):
        class _S:
            async def __aenter__(self): return "s"
            async def __aexit__(self, *a): return False
        gsf.return_value = lambda: _S()

        out = await _apply_governance(raw)

    assert len(out) == 1
    assert out[0]["text"] == "orphan"
    assert "artifact_class" not in out[0]  # falls back to default in rerank


@pytest.mark.asyncio
async def test_apply_governance_empty_input_short_circuits():
    out = await _apply_governance([])
    assert out == []


@pytest.mark.asyncio
async def test_apply_governance_no_document_ids_short_circuits():
    raw = [{"text": "a", "score": 0.9}]
    out = await _apply_governance(raw)
    assert out == raw  # untouched


# ---------------------------------------------------------------------------
# End-to-end: governance + rerank pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_governance_then_rerank_promotes_program_over_media():
    raw = [
        {"text": "media hit", "score": 0.95, "document_id": "doc-m"},
        {"text": "program hit", "score": 0.7, "document_id": "doc-p"},
    ]
    fake = {
        "doc-m": _meta(cls="media"),
        "doc-p": _meta(cls="program"),
    }
    with (
        patch("app.agents.rag_tools.get_session_factory") as gsf,
        patch("app.agents.rag_tools.fetch_document_meta", return_value=fake),
    ):
        class _S:
            async def __aenter__(self): return "s"
            async def __aexit__(self, *a): return False
        gsf.return_value = lambda: _S()

        enriched = await _apply_governance(raw)
        ranked = rerank(enriched)

    assert ranked[0].text == "program hit"  # 0.7 * 1.0 = 0.7
    assert ranked[1].text == "media hit"    # 0.95 * 0.3 = 0.285
