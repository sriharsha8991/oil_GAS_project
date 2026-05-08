"""Chat routes — RAG agent endpoint for document Q&A."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents.rag import AgentResponse, run_agent, run_agent_stream
from app.agents.rag.slash import handle as handle_slash
from app.agents.rag.title import generate_title
from app.auth.deps import AuthUser, current_user
from app.config import Settings, get_settings
from app.db.engine import get_session_factory
from app.db.models import Document, Project
from app.db.queries.chat_sessions import ensure_session, set_title
from app.db.queries.chat_messages import append_turn
from app.db.queries.context_card import build_context_card, is_corpus_empty
from app.utils.logging import get_logger
from app.utils.sse import sse_pack
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
    project_id: str | None = Field(None, description="Scope search to a specific project")
    document_id: str | None = Field(None, description="Scope search to a specific document")
    temp: bool = Field(
        False,
        description=(
            "Temp / incognito chat. Skips long-term memory capture and "
            "uses a 1-hour Redis session TTL so the conversation evaporates "
            "shortly after the user closes the tab."
        ),
    )


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
    followup: str | None = None  # deprecated; prefer ``followups``
    followups: list[str] = []
    sources: list[SourceInfo] = []
    evidence: list[EvidenceItem] = []
    attached_pages: list[dict] = []
    tool_calls_made: int = 0
    plan: dict | None = None
    assessment: dict | None = None
    meta: dict | None = None


# ---------------------------------------------------------------------------
# Sidebar persistence — index the conversation in chat_sessions so the
# user can list / rename / delete it later. Skipped entirely for temp chats.
# ---------------------------------------------------------------------------

async def _persist_session_index(
    *,
    session_id: str,
    user_id: str,
    project_id: str | None,
    user_message: str,
    assistant_message: str | None,
    settings: Settings,
    sources: list[dict] | None = None,
    meta: dict | None = None,
) -> None:
    """Best-effort: insert/touch the chat_sessions row + auto-title on turn 1
    + write the user/assistant transcript rows.

    Never raises into the request — title generation runs inline (cheap
    Flash call, ~24 output tokens) on the first turn only.
    """
    Session = get_session_factory()
    try:
        async with Session() as sess:
            row = await ensure_session(
                sess,
                session_id=session_id,
                user_id=user_id,
                project_id=project_id,
            )
            # First answered turn (message_count just went 0 -> 2). The
            # ``title_pending`` flag also gates against re-titling on
            # later turns even if the first was a clarify-only.
            first_turn = row.message_count <= 2 and row.title_pending
        # Write the transcript rows.
        async with Session() as sess:
            await append_turn(
                sess,
                session_id=session_id,
                user_id=user_id,
                user_message=user_message,
                assistant_message=assistant_message,
                sources=sources,
                meta=meta,
            )
        if first_turn and assistant_message:
            title = await generate_title(
                user_message=user_message,
                assistant_message=assistant_message,
                settings=settings,
            )
            async with Session() as sess:
                await set_title(
                    sess, session_id=session_id, user_id=user_id, title=title,
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "chat_session_persist_failed",
            session_id=session_id, error=str(exc),
        )


@router.post("", summary="Chat with documents using RAG agent", response_model=ChatResponse)
async def chat(
    request: Request,
    req: ChatRequest,
    user: AuthUser = Depends(current_user),
) -> ChatResponse:
    """Send a question and get an AI-synthesized answer grounded in documents."""
    embedder, vector_store = _get_clients(request)
    settings = get_settings()

    # Validate scope ownership before hitting Qdrant.
    if req.project_id or req.document_id:
        Session = get_session_factory()
        async with Session() as sess:
            if req.project_id:
                row = (await sess.execute(
                    select(Project).where(
                        Project.id == req.project_id,
                        Project.user_id == user.id,
                    )
                )).scalar_one_or_none()
                if row is None:
                    raise HTTPException(status_code=404, detail="project not found")
            if req.document_id:
                row = (await sess.execute(
                    select(Document).where(
                        Document.id == req.document_id,
                        Document.user_id == user.id,
                    )
                )).scalar_one_or_none()
                if row is None:
                    raise HTTPException(status_code=404, detail="document not found")

    session_id = req.session_id or str(uuid.uuid4())

    # Slash commands short-circuit the agent entirely.
    slash = await handle_slash(
        message=req.message, user_id=user.id, session_id=session_id,
    )
    if slash is not None:
        return ChatResponse(
            session_id=session_id,
            status=slash.status,
            answer=slash.reply,
            meta=slash.meta,
        )

    # ------------------------------------------------------------------
    # Build context card (used by the guard + empty-corpus check).
    # ------------------------------------------------------------------
    try:
        card = await build_context_card(
            user_id=user.id,
            role=user.role,
            email=user.email,
            display_name=user.display_name,
            organization=user.organization,
            project_id=req.project_id,
            document_id=req.document_id,
        )
    except Exception:
        logger.warning("chat_context_card_failed", exc_info=True)
        card = {}

    # Empty-corpus precondition — no point running the heavy pipeline if
    # the user has zero documents under the relevant scope. This still
    # short-circuits BEFORE the guard so we don't burn an LLM call.
    if card and is_corpus_empty(card):
        logger.info("chat_empty_corpus_softland", session_id=session_id)
        return ChatResponse(
            session_id=session_id,
            status="no_evidence",
            answer=(
                "I don't have any documents indexed for you yet. "
                "Upload a PDF and I'll be able to answer questions grounded in it."
            ),
            meta={"path": "empty_corpus_softland", "rag_invoked": False},
        )

    try:
        result: AgentResponse = await run_agent(
            req.message,
            session_id=session_id,
            settings=settings,
            embedder=embedder,
            vector_store=vector_store,
            user_id=user.id,
            card=card,
            project_id=req.project_id,
            document_id=req.document_id,
            temp=req.temp,
        )
    except Exception as exc:
        import traceback as _tb
        tb_str = _tb.format_exc()
        logger.error(
            "chat_agent_error",
            exc_info=True,
            session_id=session_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
            traceback=tb_str,
        )
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

    # Index the conversation for the sidebar (skipped for temp chats).
    if not req.temp and result.status in ("answered", "clarification_requested"):
        await _persist_session_index(
            session_id=session_id,
            user_id=user.id,
            project_id=req.project_id,
            user_message=req.message,
            assistant_message=result.answer,
            settings=settings,
            sources=[s.model_dump() for s in unique_sources],
            meta=result.meta,
        )

    return ChatResponse(
        session_id=session_id,
        status=result.status,
        answer=result.answer,
        followup=result.followup or (result.followups[0] if result.followups else None),
        followups=list(result.followups or []),
        sources=unique_sources,
        evidence=evidence,
        attached_pages=result.attached_pages,
        tool_calls_made=result.tool_calls_made,
        meta=result.meta,
    )


# ---------------------------------------------------------------------------
# Streaming endpoint — Server-Sent Events
# ---------------------------------------------------------------------------

async def _validate_chat_scope(
    *, user: AuthUser, project_id: str | None, document_id: str | None,
) -> None:
    if not (project_id or document_id):
        return
    Session = get_session_factory()
    async with Session() as sess:
        if project_id:
            row = (await sess.execute(
                select(Project).where(
                    Project.id == project_id,
                    Project.user_id == user.id,
                )
            )).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="project not found")
        if document_id:
            row = (await sess.execute(
                select(Document).where(
                    Document.id == document_id,
                    Document.user_id == user.id,
                )
            )).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="document not found")


@router.post("/stream", summary="Chat with documents using RAG agent (SSE stream)")
async def chat_stream(
    request: Request,
    req: ChatRequest,
    user: AuthUser = Depends(current_user),
) -> StreamingResponse:
    """Streaming counterpart of ``POST /chat``.

    Returns ``text/event-stream`` with a sequence of JSON-encoded SSE frames:

    - ``{"event": "status", "phase": "guard"|"rag_search"|"synthesizing", ...}``
    - ``{"event": "token",  "text": "..."}``
    - ``{"event": "done",   "session_id", "status", "answer", "sources",
                            "attached_pages", "tool_calls_made", "meta"}``

    The non-streaming ``POST /chat`` remains the canonical path; this
    endpoint is additive.
    """
    embedder, vector_store = _get_clients(request)
    settings = get_settings()

    await _validate_chat_scope(
        user=user, project_id=req.project_id, document_id=req.document_id,
    )

    session_id = req.session_id or str(uuid.uuid4())

    # Slash commands short-circuit the agent entirely. We still emit a
    # token + done frame so the FE renders the reply through the same UI
    # path it uses for streamed answers.
    slash = await handle_slash(
        message=req.message, user_id=user.id, session_id=session_id,
    )
    if slash is not None:
        async def _slash_stream():
            yield sse_pack({"event": "token", "text": slash.reply})
            yield sse_pack({
                "event": "done",
                "session_id": session_id,
                "status": slash.status,
                "answer": slash.reply,
                "sources": [],
                "attached_pages": [],
                "tool_calls_made": 0,
                "meta": slash.meta,
            })

        return StreamingResponse(
            _slash_stream(), media_type="text/event-stream",
        )

    try:
        card = await build_context_card(
            user_id=user.id,
            role=user.role,
            email=user.email,
            display_name=user.display_name,
            organization=user.organization,
            project_id=req.project_id,
            document_id=req.document_id,
        )
    except Exception:
        logger.warning("chat_stream_context_card_failed", exc_info=True)
        card = {}

    empty_softland: dict | None = None
    if card and is_corpus_empty(card):
        logger.info("chat_stream_empty_corpus_softland", session_id=session_id)
        empty_softland = {
            "event": "done",
            "session_id": session_id,
            "status": "no_evidence",
            "answer": (
                "I don't have any documents indexed for you yet. "
                "Upload a PDF and I'll be able to answer questions grounded in it."
            ),
            "sources": [],
            "attached_pages": [],
            "tool_calls_made": 0,
            "meta": {"path": "empty_corpus_softland", "rag_invoked": False},
        }

    async def event_source():
        if empty_softland is not None:
            yield sse_pack({
                "event": "token",
                "text": empty_softland["answer"],
            })
            yield sse_pack(empty_softland)
            return

        last_done: dict | None = None
        try:
            async for ev in run_agent_stream(
                req.message,
                session_id=session_id,
                settings=settings,
                embedder=embedder,
                vector_store=vector_store,
                user_id=user.id,
                card=card,
                project_id=req.project_id,
                document_id=req.document_id,
                temp=req.temp,
            ):
                if ev.get("event") == "done":
                    ev = {**ev, "session_id": session_id}
                    last_done = ev
                yield sse_pack(ev)
        except asyncio.CancelledError:
            logger.info("chat_stream_client_disconnect", session_id=session_id)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "chat_stream_agent_error",
                exc_info=True,
                session_id=session_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            yield sse_pack({
                "event": "done",
                "session_id": session_id,
                "status": "no_evidence",
                "answer": "",
                "sources": [],
                "attached_pages": [],
                "tool_calls_made": 0,
                "meta": {"path": "error", "error": "agent_failed"},
            })
            return

        # After the stream closes — index for the sidebar (non-temp only).
        if (
            not req.temp
            and last_done is not None
            and last_done.get("status") in ("answered", "clarification_requested")
        ):
            try:
                await _persist_session_index(
                    session_id=session_id,
                    user_id=user.id,
                    project_id=req.project_id,
                    user_message=req.message,
                    assistant_message=last_done.get("answer") or "",
                    settings=settings,
                    sources=last_done.get("sources") or [],
                    meta=last_done.get("meta") or {},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "chat_stream_session_persist_failed",
                    session_id=session_id, error=str(exc),
                )

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
            "Connection": "keep-alive",
        },
    )