"""Unit tests for the slash-command pre-router."""

from __future__ import annotations

from app.agents.rag.slash import HELP_TEXT, parse, _handle_scope, _handle_temp


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
