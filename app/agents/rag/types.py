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
# Conversation memory (in-process — single-worker only)
# ---------------------------------------------------------------------------

@dataclass
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class ChatSession:
    messages: list[ChatMessage] = field(default_factory=list)
    # Top-K hits surfaced in the most recent answered turn. Lets the next
    # turn's guard/synth resolve coreferent follow-ups without re-searching.
    last_sources: list[SearchResult] = field(default_factory=list)

    def add(self, role: str, content: str) -> None:
        self.messages.append(ChatMessage(role=role, content=content))
        cap = MAX_HISTORY_TURNS * 2
        if len(self.messages) > cap:
            self.messages = self.messages[-cap:]


_sessions: dict[str, ChatSession] = defaultdict(ChatSession)


def get_session(session_id: str) -> ChatSession:
    return _sessions[session_id]


# ---------------------------------------------------------------------------
# Final response (what the chat route returns)
# ---------------------------------------------------------------------------

@dataclass
class AgentResponse:
    """Final response surfaced to the route."""

    status: str  # answered | no_evidence | clarification_requested
    answer: str | None = None
    followup: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    attached_pages: list[dict[str, Any]] = field(default_factory=list)
    tool_calls_made: int = 0
    meta: dict[str, Any] | None = None
