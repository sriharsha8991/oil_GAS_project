"""Dataclasses + session store shared across the RAG modules."""

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
    # Top-K hits surfaced in the most recent answered turn. Reused as a
    # seed when the next user message is a coreferent follow-up
    # ("the deeper one", "more details", "why?") — keeps the assistant
    # anchored on the same evidence the user is implicitly referring to.
    last_chunks: list[SearchResult] = field(default_factory=list)
    last_entities: list[SearchResult] = field(default_factory=list)
    last_images: list[SearchResult] = field(default_factory=list)

    def add(self, role: str, content: str) -> None:
        self.messages.append(ChatMessage(role=role, content=content))
        cap = MAX_HISTORY_TURNS * 2
        if len(self.messages) > cap:
            self.messages = self.messages[-cap:]


_sessions: dict[str, ChatSession] = defaultdict(ChatSession)


def get_session(session_id: str) -> ChatSession:
    return _sessions[session_id]


# ---------------------------------------------------------------------------
# Planner output
# ---------------------------------------------------------------------------

@dataclass
class EntityQuery:
    q: str
    entity_type: str | None = None
    unit: str | None = None


@dataclass
class QueryPlan:
    """Structured retrieval plan emitted by the planner."""

    intent: str = "factual"  # factual|tabular|visual|procedural|comparison|listing|unknown
    # User question rewritten with all coreferences resolved against history.
    # When the user message is already self-contained, this equals it; for
    # follow-ups like "what about the deeper one?" it carries the missing
    # subject from the previous assistant turn so retrieval and synthesis
    # both work against the user's TRUE intent.
    resolved_question: str | None = None
    required_facets: list[str] = field(default_factory=list)

    chunk_queries: list[str] = field(default_factory=list)
    chunk_kinds: list[str] | None = None  # ["narrative","table"] or None

    entity_queries: list[EntityQuery] = field(default_factory=list)

    image_queries: list[str] = field(default_factory=list)
    image_types: list[str] | None = None
    use_images: bool = False

    needs_followup: bool = False
    followup_question: str | None = None

    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Retrieval bundle + assessment
# ---------------------------------------------------------------------------

@dataclass
class Bundle:
    """All hits gathered across tools for a single user turn."""

    chunks: list[SearchResult] = field(default_factory=list)
    entities: list[SearchResult] = field(default_factory=list)
    images: list[SearchResult] = field(default_factory=list)

    def all_hits(self) -> list[SearchResult]:
        return [*self.chunks, *self.entities, *self.images]

    def merge(self, other: "Bundle") -> None:
        # Dedupe by (collection, doc_name, page_num, chunk_index, table_id, canonical_id, bbox-tuple)
        seen: set[tuple] = set()
        for r in [*self.chunks, *self.entities, *self.images]:
            seen.add(_key(r))

        for r in other.chunks:
            if _key(r) not in seen:
                seen.add(_key(r))
                self.chunks.append(r)
        for r in other.entities:
            if _key(r) not in seen:
                seen.add(_key(r))
                self.entities.append(r)
        for r in other.images:
            if _key(r) not in seen:
                seen.add(_key(r))
                self.images.append(r)


def _key(r: SearchResult) -> tuple:
    bbox = tuple(r.bbox) if r.bbox else None
    return (
        r.collection, r.doc_name, r.page_num, r.chunk_index,
        r.table_id, r.canonical_id, bbox,
    )


@dataclass
class RetrievalAssessment:
    sufficient: bool
    top_score: float
    covered_facets: list[str]
    missing_facets: list[str]
    reason: str  # ok | low_score | missing_facets | no_hits


# ---------------------------------------------------------------------------
# Final response
# ---------------------------------------------------------------------------

@dataclass
class Claim:
    text: str
    source_id: str
    confidence: str = "medium"  # high|medium|low


@dataclass
class Unanswered:
    facet: str
    reason: str


@dataclass
class AgentResponse:
    """Final response surfaced to the route."""

    status: str  # answered | partial | no_evidence | clarification_requested
    answer: str | None
    claims: list[Claim] = field(default_factory=list)
    unanswered: list[Unanswered] = field(default_factory=list)
    followup: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    tool_calls_made: int = 0
    plan: dict[str, Any] | None = None
    assessment: dict[str, Any] | None = None
    attached_pages: list[dict[str, Any]] = field(default_factory=list)
