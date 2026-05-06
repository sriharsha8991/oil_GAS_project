"""Chat routes — RAG agent endpoint for document Q&A."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.agents.rag import AgentResponse, run_agent
from app.config import get_settings
from app.storage import redis_store
from app.utils.logging import get_logger
from app.vectorstore.embedder import GeminiEmbedder
from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


def _get_clients(request: Request) -> tuple[GeminiEmbedder, QdrantVectorStore]:
    embedder: GeminiEmbedder | None = getattr(request.app.state, "embedder", None)
    store: QdrantVectorStore | None = getattr(request.app.state, "qdrant", None)
    if embedder is None or store is None:
        raise HTTPException(status_code=503, detail="Gemini API key not configured")
    return embedder, store


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User message / question")
    session_id: str | None = Field(None, description="Session ID for conversation continuity")
    job_id: str | None = Field(None, description="Scope search to a specific ingestion job")
    doc_name: str | None = Field(None, description="Scope search to a specific document")


class SourceInfo(BaseModel):
    source_id: str | None = None
    text: str
    score: float
    doc_name: str | None = None
    page_num: int | None = None
    chunk_index: int | None = None
    kind: str | None = None
    table_id: str | None = None
    entity_type: str | None = None
    canonical_value: str | None = None
    unit: str | None = None
    image_type: str | None = None
    description: str | None = None
    collection: str | None = None


class EvidenceItem(BaseModel):
    """A single evidence card the UI can render alongside the answer."""

    kind: str  # "table" | "bbox_crop" | "page_image"
    source_id: str | None = None
    doc_name: str | None = None
    page_num: int | None = None
    url: str | None = None
    table_id: str | None = None
    table_markdown: str | None = None
    image_type: str | None = None
    description: str | None = None
    bbox: list[int] | None = None


class ClaimItem(BaseModel):
    text: str
    source_id: str
    confidence: str = "medium"


class UnansweredItem(BaseModel):
    facet: str
    reason: str


class ChatResponse(BaseModel):
    """Response from the chat endpoint."""

    session_id: str
    status: str  # answered | partial | no_evidence | clarification_requested
    answer: str | None
    claims: list[ClaimItem] = []
    unanswered: list[UnansweredItem] = []
    followup: str | None = None
    sources: list[SourceInfo] = []
    evidence: list[EvidenceItem] = []
    attached_pages: list[dict] = []
    tool_calls_made: int = 0
    plan: dict | None = None
    assessment: dict | None = None


@router.post("", summary="Chat with documents using RAG agent", response_model=ChatResponse)
async def chat(request: Request, req: ChatRequest) -> ChatResponse:
    """Send a question and get an AI-synthesized answer grounded in documents."""
    embedder, vector_store = _get_clients(request)
    settings = get_settings()

    # Validate scoping job_id early so a typo doesn't masquerade as
    # "no evidence" in the demo.
    if req.job_id:
        manifest = redis_store.get_job(req.job_id)
        if not manifest:
            raise HTTPException(
                status_code=404,
                detail=f"Job {req.job_id} not found. Was the document ingested?",
            )

    session_id = req.session_id or str(uuid.uuid4())

    try:
        result: AgentResponse = await run_agent(
            req.message,
            session_id=session_id,
            settings=settings,
            embedder=embedder,
            vector_store=vector_store,
            job_id=req.job_id,
            doc_name=req.doc_name,
        )
    except Exception:
        logger.error("chat_agent_error", exc_info=True, session_id=session_id)
        raise HTTPException(status_code=500, detail="Agent failed to generate a response")

    # Deduplicate sources for the wire response.
    seen: set[tuple] = set()
    unique_sources: list[SourceInfo] = []
    for s in result.sources:
        bbox = s.get("bbox")
        key = (
            s.get("doc_name"),
            s.get("page_num"),
            s.get("chunk_index"),
            s.get("collection"),
            s.get("kind"),
            s.get("table_id"),
            s.get("canonical_id"),
            tuple(bbox) if isinstance(bbox, (list, tuple)) else None,
            s.get("source_id"),  # last-resort tiebreak so Sn never collide
        )
        if key in seen:
            continue
        seen.add(key)
        unique_sources.append(SourceInfo(
            source_id=s.get("source_id"),
            text=s.get("text", ""),
            score=s.get("score", 0.0),
            doc_name=s.get("doc_name"),
            page_num=s.get("page_num"),
            chunk_index=s.get("chunk_index"),
            kind=s.get("kind"),
            table_id=s.get("table_id"),
            entity_type=s.get("entity_type"),
            canonical_value=s.get("canonical_value"),
            unit=s.get("unit"),
            image_type=s.get("image_type"),
            description=s.get("description"),
            collection=s.get("collection"),
        ))

    evidence = [EvidenceItem(**e) for e in result.evidence]
    claims = [ClaimItem(text=c.text, source_id=c.source_id, confidence=c.confidence)
              for c in result.claims]
    unanswered = [UnansweredItem(facet=u.facet, reason=u.reason)
                  for u in result.unanswered]

    return ChatResponse(
        session_id=session_id,
        status=result.status,
        answer=result.answer,
        claims=claims,
        unanswered=unanswered,
        followup=result.followup,
        sources=unique_sources,
        evidence=evidence,
        attached_pages=result.attached_pages,
        tool_calls_made=result.tool_calls_made,
        plan=result.plan,
        assessment=result.assessment,
    )
