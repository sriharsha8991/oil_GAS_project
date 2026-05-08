"""Dataclasses + session store shared across the simplified RAG modules.

The new chat is a 2-layer flow:
  Layer 1 — guard (single LLM): routes / replies / delegates.
  Layer 2 — synthesizer (single LLM, function-calls rag_search up to 3x).

There is no planner, no recovery ladder, no claim validator, no MMR/neighbor
expansion. Whatever evidence the synthesizer pulls is what the user sees.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.agents.rag_tools import SearchResult

MAX_HISTORY_TURNS = 10


# ---------------------------------------------------------------------------
# Conversation memory (Redis-backed; see app/agents/rag/sessions.py)
# ---------------------------------------------------------------------------

@dataclass
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class ChatSession:
    messages: list[ChatMessage] = field(default_factory=list)
    # Top-K hits surfaced in the most recent answered turn. In-memory only;
    # not persisted to Redis (kept for transient cross-call use).
    last_sources: list[SearchResult] = field(default_factory=list)
    # Rolling summary of older turns once history exceeds MAX_HISTORY_TURNS.
    summary: str = ""
    # Sticky subject carried across follow-ups: well, document, formation, etc.
    current_subject: dict[str, str] = field(default_factory=dict)

    def add(self, role: str, content: str) -> None:
        self.messages.append(ChatMessage(role=role, content=content))
        cap = MAX_HISTORY_TURNS * 2
        if len(self.messages) > cap:
            self.messages = self.messages[-cap:]


# In-process fallback cache. The canonical store is Redis (see
# ``app.agents.rag.sessions``). This dict is only used when Redis is
# unreachable so chat keeps working in degraded mode.
_sessions: dict[str, ChatSession] = defaultdict(ChatSession)


def get_session(session_id: str) -> ChatSession:
    """In-process fallback (sync). Prefer ``get_session_async``."""
    return _sessions[session_id]


# ---------------------------------------------------------------------------
# Final response (what the chat route returns)
# ---------------------------------------------------------------------------

@dataclass
class AgentResponse:
    """Final response surfaced to the route."""

    status: str  # answered | no_evidence | clarification_requested
    answer: str | None = None
    followup: str | None = None  # legacy single-suggestion field; kept for one release
    followups: list[str] = field(default_factory=list)  # 0..3 suggestion chips
    sources: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    attached_pages: list[dict[str, Any]] = field(default_factory=list)
    tool_calls_made: int = 0
    meta: dict[str, Any] | None = None
