"""Tests for chat_sessions queries + auto-title helper.

Pure-python unit tests with the DB mocked or in-memory; full integration
is covered by the live 401-gated smoke (see deployment notes).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


# ---------------------------------------------------------------------------
# title.generate_title — Flash-backed with safe fallbacks
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.text = text


def _client_with_text(text: str) -> SimpleNamespace:
    inner = SimpleNamespace(generate_content=AsyncMock(return_value=_FakeResp(text)))
    return SimpleNamespace(aio=SimpleNamespace(models=inner))


def _settings():
    return SimpleNamespace(chat_synth_model="gemini-test", gemini_api_key="x")


@pytest.mark.asyncio
async def test_generate_title_happy_path_strips_quotes_and_caps_length():
    from app.agents.rag.title import generate_title

    long = '"Sajaa 6 EOWR — Kicks, NPT and Casing Decisions"' + " padding" * 50
    out = await generate_title(
        user_message="What happened in Sajaa 6?",
        assistant_message="Several events.",
        settings=_settings(),
        client=_client_with_text(long),
    )
    # Quote stripping
    assert not out.startswith('"') and not out.endswith('"')
    assert "Sajaa 6 EOWR" in out
    # Length cap with ellipsis
    assert len(out) <= 81  # 80 + ellipsis


@pytest.mark.asyncio
async def test_generate_title_falls_back_when_llm_returns_new_chat():
    from app.agents.rag.title import generate_title

    out = await generate_title(
        user_message="Tell me about formation X tops in this report",
        assistant_message="Here are the tops.",
        settings=_settings(),
        client=_client_with_text("New chat"),
    )
    # Falls back to a truncation of the user message instead of "New chat".
    assert out != "New chat"
    assert out.startswith("Tell me about formation X")


@pytest.mark.asyncio
async def test_generate_title_falls_back_on_llm_error():
    from app.agents.rag.title import generate_title

    bad = SimpleNamespace(generate_content=AsyncMock(side_effect=RuntimeError("boom")))
    client = SimpleNamespace(aio=SimpleNamespace(models=bad))
    out = await generate_title(
        user_message="What is the formation depth?",
        assistant_message="It is 1234m.",
        settings=_settings(),
        client=client,
    )
    assert out.startswith("What is the formation depth")


@pytest.mark.asyncio
async def test_generate_title_empty_user_message_yields_default():
    from app.agents.rag.title import generate_title

    out = await generate_title(
        user_message="",
        assistant_message="...",
        settings=_settings(),
        client=_client_with_text("Should Not Be Used"),
    )
    assert out == "New chat"


# ---------------------------------------------------------------------------
# queries/chat_sessions — strict project/user scoping + ownership checks
# ---------------------------------------------------------------------------


class _FakeSession:
    """Captures the SQL/expressions executed and returns canned values."""

    def __init__(self, scalars=None, scalar_one=None, rowcount=0):
        self.executed: list = []
        self._scalars = scalars or []
        self._scalar_one = scalar_one
        self._rowcount = rowcount
        self.commits = 0

    async def execute(self, stmt):
        self.executed.append(stmt)
        return _FakeResult(self._scalars, self._scalar_one, self._rowcount)

    async def commit(self):
        self.commits += 1


class _FakeResult:
    def __init__(self, scalars, scalar_one, rowcount):
        self._scalars = scalars
        self._scalar_one = scalar_one
        self.rowcount = rowcount

    def scalars(self):
        return SimpleNamespace(all=lambda: self._scalars)

    def scalar_one(self):
        return self._scalar_one

    def scalar_one_or_none(self):
        return self._scalar_one


@pytest.mark.asyncio
async def test_list_sessions_current_scope_filters_by_project_id():
    from app.db.queries.chat_sessions import list_sessions

    sess = _FakeSession(scalars=[])
    await list_sessions(
        sess, user_id="11111111-1111-1111-1111-111111111111",
        project_id="22222222-2222-2222-2222-222222222222",
        project_scope="current",
    )
    sql = str(sess.executed[0].compile(compile_kwargs={"literal_binds": False}))
    assert "PROJECT_ID =" in sql.upper()
    assert "ARCHIVED_AT IS NULL" in sql.upper()


@pytest.mark.asyncio
async def test_list_sessions_current_scope_with_null_project_filters_is_null():
    from app.db.queries.chat_sessions import list_sessions

    sess = _FakeSession(scalars=[])
    await list_sessions(
        sess, user_id="11111111-1111-1111-1111-111111111111",
        project_id=None, project_scope="current",
    )
    sql = str(sess.executed[0].compile(compile_kwargs={"literal_binds": False}))
    assert "PROJECT_ID IS NULL" in sql.upper()


@pytest.mark.asyncio
async def test_list_sessions_all_scope_omits_project_filter():
    from app.db.queries.chat_sessions import list_sessions

    sess = _FakeSession(scalars=[])
    await list_sessions(
        sess, user_id="11111111-1111-1111-1111-111111111111",
        project_id="22222222-2222-2222-2222-222222222222",
        project_scope="all",
    )
    sql = str(sess.executed[0].compile(compile_kwargs={"literal_binds": False})).upper()
    assert "PROJECT_ID =" not in sql
    assert "PROJECT_ID IS NULL" not in sql


@pytest.mark.asyncio
async def test_list_sessions_include_archived_drops_archive_filter():
    from app.db.queries.chat_sessions import list_sessions

    sess = _FakeSession(scalars=[])
    await list_sessions(
        sess, user_id="11111111-1111-1111-1111-111111111111",
        include_archived=True,
    )
    sql = str(sess.executed[0].compile(compile_kwargs={"literal_binds": False})).upper()
    assert "ARCHIVED_AT IS NULL" not in sql


@pytest.mark.asyncio
async def test_set_title_rejects_blank_and_does_not_execute():
    from app.db.queries.chat_sessions import set_title

    sess = _FakeSession(rowcount=1)
    ok = await set_title(
        sess, session_id="00000000-0000-0000-0000-000000000000",
        user_id="11111111-1111-1111-1111-111111111111", title="   ",
    )
    assert ok is False
    assert sess.executed == []  # no UPDATE issued
    assert sess.commits == 0


@pytest.mark.asyncio
async def test_set_title_truncates_to_120_chars():
    from app.db.queries.chat_sessions import set_title

    sess = _FakeSession(rowcount=1)
    long = "A" * 500
    ok = await set_title(
        sess, session_id="00000000-0000-0000-0000-000000000000",
        user_id="11111111-1111-1111-1111-111111111111", title=long,
    )
    assert ok is True
    compiled = sess.executed[0].compile()
    title_param = next(
        (v for k, v in compiled.params.items() if "title" in k.lower()),
        None,
    )
    assert title_param is not None
    assert len(title_param) == 120
    assert title_param == "A" * 120


@pytest.mark.asyncio
async def test_delete_session_row_filters_by_user_id():
    from app.db.queries.chat_sessions import delete_session_row

    sess = _FakeSession(rowcount=1)
    n = await delete_session_row(
        sess, session_id="00000000-0000-0000-0000-000000000000",
        user_id="11111111-1111-1111-1111-111111111111",
    )
    assert n == 1
    sql = str(sess.executed[0].compile(compile_kwargs={"literal_binds": False})).upper()
    assert "USER_ID =" in sql
    assert sess.commits == 1
