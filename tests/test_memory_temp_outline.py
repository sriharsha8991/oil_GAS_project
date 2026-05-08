"""Edge-case regression tests for memory toggle + project scope + temp chat.

These are pure-Python unit tests with the DB mocked. Live DB integration is
covered by the existing 401-gated curl smokes in deployment notes.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.rag import agent as agent_mod
from app.agents.rag.synthesizer import MAX_FOLLOWUPS


# ---------------------------------------------------------------------------
# generate_followups — output dedup, length cap, defensive parsing
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.text = text


def _client_with_text(text: str) -> SimpleNamespace:
    inner = SimpleNamespace(generate_content=AsyncMock(return_value=_FakeResp(text)))
    aio = SimpleNamespace(models=inner)
    return SimpleNamespace(aio=aio)


def _settings():
    return SimpleNamespace(chat_synth_model="gemini-test")


@pytest.mark.asyncio
async def test_followups_dedup_and_cap():
    from app.agents.rag.synthesizer import generate_followups

    payload = (
        '{"followups": ["What is the depth?", "what is the depth", '
        '"How many casing strings?", "Why was NPT high?", "extra one"]}'
    )
    out = await generate_followups(
        client=_client_with_text(payload), settings=_settings(),
        user_message="tell me about Sajaa 6", answer="It is a well.",
        has_sources=True,
    )
    assert len(out) <= MAX_FOLLOWUPS
    # Dedup case-insensitively / whitespace-insensitively via prompt — we
    # at minimum dedupe exact stripped duplicates after rstrip-of-punct.
    assert len(set(out)) == len(out)


@pytest.mark.asyncio
async def test_followups_bad_json_returns_empty():
    from app.agents.rag.synthesizer import generate_followups

    out = await generate_followups(
        client=_client_with_text("not json at all"),
        settings=_settings(),
        user_message="x", answer="y", has_sources=False,
    )
    assert out == []


@pytest.mark.asyncio
async def test_followups_llm_error_returns_empty():
    from app.agents.rag.synthesizer import generate_followups

    bad_inner = SimpleNamespace(
        generate_content=AsyncMock(side_effect=RuntimeError("network down"))
    )
    client = SimpleNamespace(aio=SimpleNamespace(models=bad_inner))
    out = await generate_followups(
        client=client, settings=_settings(),
        user_message="x", answer="y", has_sources=True,
    )
    assert out == []


@pytest.mark.asyncio
async def test_followups_skipped_on_empty_answer():
    from app.agents.rag.synthesizer import generate_followups

    inner = SimpleNamespace(generate_content=AsyncMock())
    client = SimpleNamespace(aio=SimpleNamespace(models=inner))
    out = await generate_followups(
        client=client, settings=_settings(),
        user_message="x", answer="   ", has_sources=False,
    )
    assert out == []
    inner.generate_content.assert_not_called()


# ---------------------------------------------------------------------------
# Memory capture dispatch — temp mode short-circuits LTM
# ---------------------------------------------------------------------------


def test_dispatch_memory_capture_skipped_when_temp(monkeypatch):
    called = {"n": 0}

    def fake_delay(**kwargs):
        called["n"] += 1

    fake_task = SimpleNamespace(delay=fake_delay)
    fake_module = SimpleNamespace(extract_user_memory=fake_task)
    import sys
    monkeypatch.setitem(sys.modules, "app.workers.tasks.memory", fake_module)

    agent_mod._dispatch_memory_capture(
        user_id="u1", session_id="s1",
        user_message="hello world this is a long enough message",
        assistant_message="and a substantive enough answer back here too",
        status="answered", project_id=None, temp=True,
    )
    assert called["n"] == 0


def test_dispatch_memory_capture_passes_project_id(monkeypatch):
    captured = {}

    def fake_delay(**kwargs):
        captured.update(kwargs)

    fake_task = SimpleNamespace(delay=fake_delay)
    fake_module = SimpleNamespace(extract_user_memory=fake_task)
    import sys
    monkeypatch.setitem(sys.modules, "app.workers.tasks.memory", fake_module)

    agent_mod._dispatch_memory_capture(
        user_id="u1", session_id="s1",
        user_message="hello world this is a long enough message",
        assistant_message="and a substantive enough answer back here too",
        status="answered", project_id="proj-abc", temp=False,
    )
    assert captured.get("project_id") == "proj-abc"
    assert captured.get("user_id") == "u1"


def test_dispatch_memory_capture_skipped_on_no_evidence(monkeypatch):
    called = {"n": 0}

    def fake_delay(**kwargs):
        called["n"] += 1

    fake_task = SimpleNamespace(delay=fake_delay)
    fake_module = SimpleNamespace(extract_user_memory=fake_task)
    import sys
    monkeypatch.setitem(sys.modules, "app.workers.tasks.memory", fake_module)

    agent_mod._dispatch_memory_capture(
        user_id="u1", session_id="s1",
        user_message="hello", assistant_message="some answer",
        status="no_evidence", project_id=None, temp=False,
    )
    assert called["n"] == 0


# ---------------------------------------------------------------------------
# _save — temp uses TEMP_TTL_SECONDS, normal uses None
# ---------------------------------------------------------------------------


def test_save_uses_temp_ttl_when_temp(monkeypatch):
    captured = {}

    async def fake_save(session_id, session, *, ttl=None):
        captured["ttl"] = ttl

    monkeypatch.setattr(agent_mod, "save_session_async", fake_save)
    asyncio.run(agent_mod._save("sid", object(), temp=True))
    assert captured["ttl"] == agent_mod.TEMP_TTL_SECONDS


def test_save_uses_default_ttl_when_not_temp(monkeypatch):
    captured = {}

    async def fake_save(session_id, session, *, ttl=None):
        captured["ttl"] = ttl

    monkeypatch.setattr(agent_mod, "save_session_async", fake_save)
    asyncio.run(agent_mod._save("sid", object(), temp=False))
    assert captured["ttl"] is None


# ---------------------------------------------------------------------------
# Memory queries — strict project isolation in fetch / list / forget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_top_memories_filters_global_by_default():
    """When project_id is None, the WHERE clause must add project_id IS NULL."""
    from app.db.queries.memories import fetch_top_memories
    from app.db.models import UserMemory

    fake_sess = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))

    await fetch_top_memories(fake_sess, user_id="u1")
    stmt = fake_sess.execute.call_args.args[0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "PROJECT_ID IS NULL" in sql.upper()


@pytest.mark.asyncio
async def test_fetch_top_memories_filters_by_project_when_set():
    from app.db.queries.memories import fetch_top_memories

    fake_sess = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
    await fetch_top_memories(fake_sess, user_id="u1", project_id="p-1")
    stmt = fake_sess.execute.call_args.args[0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "PROJECT_ID =" in sql.upper()


@pytest.mark.asyncio
async def test_forget_memory_rejects_invalid_scope():
    from app.db.queries.memories import forget_memory

    fake_sess = SimpleNamespace(execute=AsyncMock())
    out = await forget_memory(fake_sess, user_id="u1", scope="bogus", key="k")
    assert out is False
    fake_sess.execute.assert_not_called()


@pytest.mark.asyncio
async def test_forget_memory_rejects_dismissed_scope():
    from app.db.queries.memories import forget_memory

    fake_sess = SimpleNamespace(execute=AsyncMock())
    out = await forget_memory(fake_sess, user_id="u1", scope="dismissed", key="k")
    assert out is False
    fake_sess.execute.assert_not_called()


@pytest.mark.asyncio
async def test_forget_all_scoped_to_project_only():
    from app.db.queries.memories import forget_all_memories

    fake_sess = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(rowcount=0))
    )
    await forget_all_memories(
        fake_sess, user_id="u1", project_id="p-1", project_scope="current",
    )
    stmt = fake_sess.execute.call_args.args[0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    assert "PROJECT_ID =" in sql.upper()


@pytest.mark.asyncio
async def test_forget_all_with_scope_all_omits_project_filter():
    from app.db.queries.memories import forget_all_memories

    fake_sess = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(rowcount=0))
    )
    await forget_all_memories(
        fake_sess, user_id="u1", project_id=None, project_scope="all",
    )
    stmt = fake_sess.execute.call_args.args[0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": False}))
    # No project_id constraint in 'all' mode.
    cleaned = sql.upper()
    assert "PROJECT_ID IS NULL" not in cleaned
    assert "PROJECT_ID =" not in cleaned


@pytest.mark.asyncio
async def test_list_memories_invalid_scope_returns_empty():
    from app.db.queries.memories import list_memories

    fake_sess = SimpleNamespace(execute=AsyncMock())
    out = await list_memories(fake_sess, user_id="u1", scope="garbage")
    assert out == []
    fake_sess.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Document outline parser — heading + page tagging edge cases
# ---------------------------------------------------------------------------


def test_outline_heading_regex_skips_non_atx_headings():
    """Only #, ##, ### are headings; longer hash sequences are ignored."""
    from app.db.queries.document_outline import _HEADING_RE

    md = (
        "## Real Section\n"
        "#### Too Deep Heading\n"
        "Some body text\n"
        "# Top Level\n"
    )
    found = [m.group(2) for m in _HEADING_RE.finditer(md)]
    assert "Real Section" in found
    assert "Top Level" in found
    assert "Too Deep Heading" not in found


def test_outline_page_marker_regex_extracts_zero_indexed_pages():
    from app.db.queries.document_outline import _PAGE_MARKER_RE

    md = (
        "---\n## Page 0\nopening text\n"
        "---\n## Page 12\nmidway content\n"
    )
    matches = [int(m.group(1)) for m in _PAGE_MARKER_RE.finditer(md)]
    assert matches == [0, 12]
