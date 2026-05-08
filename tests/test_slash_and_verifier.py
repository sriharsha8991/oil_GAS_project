"""Unit tests for the slash-command pre-router and the source verifier."""

from __future__ import annotations

import pytest

from app.agents.rag.slash import HELP_TEXT, parse, _handle_scope, _handle_temp
from app.agents.rag.verifier import VerificationReport, _strip_citation, verify_answer


# ---------------------------------------------------------------------------
# Slash parser & sync handlers
# ---------------------------------------------------------------------------


class TestSlashParse:
    def test_non_slash_returns_none(self):
        assert parse("hello") is None
        assert parse("") is None
        assert parse("  what's up?") is None

    def test_simple_command(self):
        assert parse("/help") == ("help", "")

    def test_command_with_args(self):
        assert parse("/forget all") == ("forget", "all")

    def test_command_with_uuid_arg(self):
        cmd, args = parse("/scope project=11111111-2222-3333-4444-555555555555")
        assert cmd == "scope"
        assert args == "project=11111111-2222-3333-4444-555555555555"

    def test_help_text_present(self):
        assert "/help" in HELP_TEXT
        assert "/forget" in HELP_TEXT


class TestScopeHandler:
    def test_clear(self):
        r = _handle_scope("clear")
        assert r.meta["cmd"] == "scope_clear"

    def test_valid_uuid(self):
        r = _handle_scope("project=11111111-2222-3333-4444-555555555555")
        assert r.meta.get("project_id") == "11111111-2222-3333-4444-555555555555"

    def test_invalid_uuid(self):
        r = _handle_scope("project=not-a-uuid")
        assert r.meta.get("ok") is False

    def test_usage_on_garbage(self):
        r = _handle_scope("blah")
        assert "Usage" in r.reply


class TestTempHandler:
    def test_on(self):
        r = _handle_temp("on")
        assert r.meta["value"] is True

    def test_off(self):
        r = _handle_temp("off")
        assert r.meta["value"] is False

    def test_invalid(self):
        r = _handle_temp("maybe")
        assert r.meta.get("ok") is False


# ---------------------------------------------------------------------------
# Verifier helpers (no LLM)
# ---------------------------------------------------------------------------


class TestStripCitation:
    def test_removes_marker(self):
        assert _strip_citation("foo [S1] bar", "S1") == "foo bar"

    def test_idempotent_when_absent(self):
        assert _strip_citation("foo bar", "S5") == "foo bar"

    def test_strips_multiple(self):
        assert _strip_citation("a [S2] b [S2] c", "S2") == "a b c"


# ---------------------------------------------------------------------------
# verify_answer end-to-end (with stubbed Flash call)
# ---------------------------------------------------------------------------


class _StubResp:
    def __init__(self, text: str) -> None:
        self.text = text


class _StubModels:
    def __init__(self, payload: str):
        self._payload = payload

    async def generate_content(self, **_kwargs):
        return _StubResp(self._payload)


class _StubAio:
    def __init__(self, payload: str):
        self.models = _StubModels(payload)


class _StubClient:
    def __init__(self, payload: str):
        self.aio = _StubAio(payload)


@pytest.mark.asyncio
async def test_verify_skips_when_no_citations():
    from app.config import Settings
    settings = Settings(gemini_api_key="x")
    rewritten, report = await verify_answer(
        client=_StubClient("[]"),
        settings=settings,
        answer="No citations here.",
        sources=[{"source_id": "S1", "text": "x"}],
    )
    assert rewritten == "No citations here."
    assert report.skipped is True


@pytest.mark.asyncio
async def test_verify_strips_unknown_citations():
    from app.config import Settings
    settings = Settings(gemini_api_key="x")
    # Model returns no verdicts but answer cites S9 which we never returned.
    rewritten, report = await verify_answer(
        client=_StubClient("[]"),
        settings=settings,
        answer="Claim citing nothing real [S9].",
        sources=[{"source_id": "S1", "text": "x"}],
    )
    assert "S9" not in rewritten
    assert "S9" in report.unknown_ids


@pytest.mark.asyncio
async def test_verify_strips_unsupported_citations():
    from app.config import Settings
    settings = Settings(gemini_api_key="x")
    payload = (
        '[{"source_id": "S1", "supported": false, "reason": "off-topic"},'
        ' {"source_id": "S2", "supported": true, "reason": "match"}]'
    )
    rewritten, report = await verify_answer(
        client=_StubClient(payload),
        settings=settings,
        answer="Claim A [S1]. Claim B [S2].",
        sources=[
            {"source_id": "S1", "text": "irrelevant snippet"},
            {"source_id": "S2", "text": "supporting snippet"},
        ],
    )
    assert "S1" not in rewritten
    assert "S2" in rewritten
    assert "S1" in report.unsupported_ids
    assert "S2" not in report.unsupported_ids


@pytest.mark.asyncio
async def test_verify_handles_llm_failure_gracefully():
    from app.config import Settings

    class BoomClient:
        class _Aio:
            class _Models:
                async def generate_content(self, **_kwargs):
                    raise RuntimeError("network down")
            models = _Models()
        aio = _Aio()

    settings = Settings(gemini_api_key="x")
    rewritten, report = await verify_answer(
        client=BoomClient(),
        settings=settings,
        answer="Claim [S1].",
        sources=[{"source_id": "S1", "text": "snippet"}],
    )
    # Answer kept as-is; report records the error.
    assert rewritten == "Claim [S1]."
    assert report.error == "RuntimeError"


def test_verification_report_to_dict_roundtrip():
    r = VerificationReport(cited_ids=["S1"], unsupported_ids=["S1"], checked=1)
    d = r.to_dict()
    assert d["cited_ids"] == ["S1"]
    assert d["unsupported_ids"] == ["S1"]
    assert d["checked"] == 1
    assert d["skipped"] is False
