"""Chat routes — RAG agent endpoint for document Q&A."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agents.rag_agent import AgentResponse, run_agent
from app.config import get_settings
from app.utils.logging import get_logger
from app.vectorstore.embedder import GeminiEmbedder
from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

# Lazy singletons — same pattern as search.py
_embedder: GeminiEmbedder | None = None
_vector_store: QdrantVectorStore | None = None


def _get_clients() -> tuple[GeminiEmbedder, QdrantVectorStore]:
    global _embedder, _vector_store  # noqa: PLW0603
    if _embedder is None:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise HTTPException(status_code=503, detail="Gemini API key not configured")
        _embedder = GeminiEmbedder(settings)
        _vector_store = QdrantVectorStore(settings)
    return _embedder, _vector_store  # type: ignore[return-value]


class ChatRequest(BaseModel):
    """Request body for the chat endpoint."""

    message: str = Field(..., min_length=1, description="User message / question")
    session_id: str | None = Field(None, description="Session ID for conversation continuity")
    job_id: str | None = Field(None, description="Scope search to a specific ingestion job")
    doc_name: str | None = Field(None, description="Scope search to a specific document")


class SourceInfo(BaseModel):
    text: str
    score: float
    doc_name: str | None = None
    page_num: int | None = None
    chunk_index: int | None = None


class ChatResponse(BaseModel):
    """Response from the chat endpoint."""

    session_id: str
    answer: str
    sources: list[SourceInfo]
    tool_calls_made: int


@router.post("", summary="Chat with documents using RAG agent", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Send a question and get an AI-synthesized answer grounded in documents.

    The agent will:
    1. Analyze your question
    2. Search relevant document chunks (up to 3 searches per turn)
    3. Synthesize a grounded answer with source citations
    """
    embedder, vector_store = _get_clients()
    settings = get_settings()

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

    # Deduplicate sources by (doc_name, page_num, chunk_index)
    seen: set[tuple] = set()
    unique_sources: list[SourceInfo] = []
    for s in result.sources:
        key = (s.get("doc_name"), s.get("page_num"), s.get("chunk_index"))
        if key not in seen:
            seen.add(key)
            unique_sources.append(SourceInfo(**s))

    return ChatResponse(
        session_id=session_id,
        answer=result.answer,
        sources=unique_sources,
        tool_calls_made=result.tool_calls_made,
    )
