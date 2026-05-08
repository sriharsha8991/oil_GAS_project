"""RAG agent — 2-layer orchestrator.

Flow:
  1. Free regex pre-filter   — catches greetings/thanks at zero cost.
  2. Guard LLM (Layer 1)     — single Flash call. Either answers directly
                               (chitchat / app help / account / catalog) or
                               delegates to the synthesizer with a resolved
                               domain query.
  3. Synthesizer (Layer 2)   — Flash with function-calling on rag_search
                               (max 3 calls). Decomposes if needed and
                               crafts the grounded answer.

That is the entire chat pipeline. There is no planner, no recovery
ladder, no claim validator, no MMR/neighbor expansion.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from google import genai

from app.agents.rag.guard import guard, prefilter
from app.agents.rag.sessions import get_session_async, save_session_async
from app.agents.rag.stm import (
    maybe_compress_history,
    render_history_block,
    render_subject_line,
    update_current_subject,
)
from app.agents.rag.synthesizer import synthesize, synthesize_stream
from app.agents.rag.types import AgentResponse
from app.config import Settings
from app.storage.supabase_storage import SupabaseStorage
from app.utils.logging import get_logger
from app.vectorstore.embedder import GeminiEmbedder
from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)


async def _post_turn_stm(
    *,
    session_id: str,
    session,
    user_message: str,
    assistant_message: str,
    settings: Settings,
    client: genai.Client,
    update_subject: bool,
) -> None:
    """Best-effort post-turn STM maintenance: subject + rolling summary + save."""
    try:
        if update_subject and assistant_message:
            await update_current_subject(
                session=session,
                user_message=user_message,
                assistant_message=assistant_message,
                settings=settings,
                client=client,
            )
        await maybe_compress_history(
            session=session, settings=settings, client=client,
        )
    finally:
        await save_session_async(session_id, session)


async def run_agent(
    user_message: str,
    *,
    session_id: str,
    settings: Settings,
    embedder: GeminiEmbedder,
    vector_store: QdrantVectorStore,
    user_id: str,
    card: dict | None = None,
    project_id: str | None = None,
    document_id: str | None = None,
) -> AgentResponse:
    session = await get_session_async(session_id)
    history_text = render_history_block(session)
    subject_line = render_subject_line(session.current_subject)

    # ------------------------------------------------------------------
    # Layer 0 — free pre-filter.
    # ------------------------------------------------------------------
    pre = prefilter(user_message)
    if pre is not None:
        logger.info("agent_prefilter_hit", session_id=session_id)
        session.add("user", user_message)
        session.add("assistant", pre.reply)
        await save_session_async(session_id, session)
        return AgentResponse(
            status="answered",
            answer=pre.reply,
            tool_calls_made=0,
            meta={
                "path": "prefilter",
                "rag_invoked": False,
                "guard_input_tokens": 0,
                "guard_output_tokens": 0,
            },
        )

    client = genai.Client(api_key=settings.gemini_api_key)

    # ------------------------------------------------------------------
    # Layer 1 — guard.
    # ------------------------------------------------------------------
    decision = await guard(
        message=user_message, history_text=history_text,
        card=card or {}, settings=settings, client=client,
        current_subject=subject_line,
    )
    logger.info(
        "agent_guard",
        session_id=session_id,
        action=decision.action,
        tokens_in=decision.input_tokens,
        tokens_out=decision.output_tokens,
    )

    if decision.action == "reply":
        session.add("user", user_message)
        session.add("assistant", decision.reply)
        await save_session_async(session_id, session)
        return AgentResponse(
            status="answered",
            answer=decision.reply,
            tool_calls_made=1,
            meta={
                "path": "guard_reply",
                "rag_invoked": False,
                "guard_input_tokens": decision.input_tokens,
                "guard_output_tokens": decision.output_tokens,
            },
        )

    if decision.action == "clarify":
        session.add("user", user_message)
        session.add("assistant", decision.clarification)
        await save_session_async(session_id, session)
        return AgentResponse(
            status="clarification_requested",
            answer=decision.clarification,
            followup=decision.clarification,
            tool_calls_made=1,
            meta={
                "path": "guard_clarify",
                "rag_invoked": False,
                "guard_input_tokens": decision.input_tokens,
                "guard_output_tokens": decision.output_tokens,
            },
        )

    # ------------------------------------------------------------------
    # Layer 2 — synthesizer with rag_search tool.
    # ------------------------------------------------------------------
    storage = SupabaseStorage(settings)
    response = await synthesize(
        client=client, settings=settings,
        user_message=user_message,
        resolved_query=decision.query or user_message,
        history_text=history_text,
        embedder=embedder, vector_store=vector_store,
        user_id=user_id, project_id=project_id, document_id=document_id,
        storage=storage,
    )
    # Tool calls = guard (1) + each rag_search invocation (already counted
    # by the synthesizer). The synth call itself is internal model work.
    response.tool_calls_made = (response.tool_calls_made or 0) + 1
    response.meta = {
        **(response.meta or {}),
        "path": "guard_then_synth",
        "rag_invoked": True,
        "guard_input_tokens": decision.input_tokens,
        "guard_output_tokens": decision.output_tokens,
    }

    if response.answer:
        session.add("user", user_message)
        session.add("assistant", response.answer)

    # Best-effort post-turn STM upkeep — fire-and-forget so it doesn't
    # delay the user response. The task will write the session itself.
    asyncio.create_task(_post_turn_stm(
        session_id=session_id, session=session,
        user_message=user_message,
        assistant_message=response.answer or "",
        settings=settings, client=client,
        update_subject=response.status == "answered",
    ))

    logger.info(
        "agent_done",
        session_id=session_id,
        status=response.status,
        sources=len(response.sources),
        tool_calls=response.tool_calls_made,
    )
    return response


async def run_agent_stream(
    user_message: str,
    *,
    session_id: str,
    settings: Settings,
    embedder: GeminiEmbedder,
    vector_store: QdrantVectorStore,
    user_id: str,
    card: dict | None = None,
    project_id: str | None = None,
    document_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Streaming counterpart of ``run_agent``.

    Yields dict events shaped for the SSE wire format:
      ``{"event": "status", "phase": ...}``
      ``{"event": "token",  "text": ...}``
      ``{"event": "done",   "status": ..., "answer": ..., "sources": [...], ...}``
    """
    session = await get_session_async(session_id)
    history_text = render_history_block(session)
    subject_line = render_subject_line(session.current_subject)

    # ------------------------------------------------------------------
    # Layer 0 — free pre-filter.
    # ------------------------------------------------------------------
    pre = prefilter(user_message)
    if pre is not None:
        logger.info("agent_stream_prefilter_hit", session_id=session_id)
        session.add("user", user_message)
        session.add("assistant", pre.reply)
        await save_session_async(session_id, session)
        yield {"event": "token", "text": pre.reply}
        yield {
            "event": "done",
            "status": "answered",
            "answer": pre.reply,
            "sources": [],
            "attached_pages": [],
            "tool_calls_made": 0,
            "meta": {
                "path": "prefilter",
                "rag_invoked": False,
                "guard_input_tokens": 0,
                "guard_output_tokens": 0,
            },
        }
        return

    client = genai.Client(api_key=settings.gemini_api_key)

    # ------------------------------------------------------------------
    # Layer 1 — guard.
    # ------------------------------------------------------------------
    yield {"event": "status", "phase": "guard"}
    decision = await guard(
        message=user_message, history_text=history_text,
        card=card or {}, settings=settings, client=client,
        current_subject=subject_line,
    )
    logger.info(
        "agent_stream_guard",
        session_id=session_id,
        action=decision.action,
        tokens_in=decision.input_tokens,
        tokens_out=decision.output_tokens,
    )

    if decision.action == "reply":
        session.add("user", user_message)
        session.add("assistant", decision.reply)
        await save_session_async(session_id, session)
        yield {"event": "token", "text": decision.reply}
        yield {
            "event": "done",
            "status": "answered",
            "answer": decision.reply,
            "sources": [],
            "attached_pages": [],
            "tool_calls_made": 1,
            "meta": {
                "path": "guard_reply",
                "rag_invoked": False,
                "guard_input_tokens": decision.input_tokens,
                "guard_output_tokens": decision.output_tokens,
            },
        }
        return

    if decision.action == "clarify":
        session.add("user", user_message)
        session.add("assistant", decision.clarification)
        await save_session_async(session_id, session)
        yield {"event": "token", "text": decision.clarification}
        yield {
            "event": "done",
            "status": "clarification_requested",
            "answer": decision.clarification,
            "followup": decision.clarification,
            "sources": [],
            "attached_pages": [],
            "tool_calls_made": 1,
            "meta": {
                "path": "guard_clarify",
                "rag_invoked": False,
                "guard_input_tokens": decision.input_tokens,
                "guard_output_tokens": decision.output_tokens,
            },
        }
        return

    # ------------------------------------------------------------------
    # Layer 2 — synthesizer (streaming).
    # ------------------------------------------------------------------
    storage = SupabaseStorage(settings)
    final_answer = ""
    final_done: dict[str, Any] | None = None
    async for ev in synthesize_stream(
        client=client, settings=settings,
        user_message=user_message,
        resolved_query=decision.query or user_message,
        history_text=history_text,
        embedder=embedder, vector_store=vector_store,
        user_id=user_id, project_id=project_id, document_id=document_id,
        storage=storage,
    ):
        if ev.get("event") == "done":
            final_done = ev
            final_answer = ev.get("answer") or ""
            break
        yield ev

    if final_done is None:
        # Stream aborted before producing a done event — emit a deterministic
        # error event so the FE can render something rather than hang.
        final_done = {
            "event": "done",
            "status": "no_evidence",
            "answer": "",
            "sources": [],
            "attached_pages": [],
            "tool_calls_made": 0,
            "meta": {"rag_calls": 0, "include_images": False, "total_hits": 0},
        }

    if final_answer:
        session.add("user", user_message)
        session.add("assistant", final_answer)

    # Best-effort post-turn STM upkeep — fire-and-forget so it doesn't
    # delay the user response. The task will write the session itself.
    asyncio.create_task(_post_turn_stm(
        session_id=session_id, session=session,
        user_message=user_message,
        assistant_message=final_answer,
        settings=settings, client=client,
        update_subject=(final_done.get("status") == "answered"),
    ))

    final_done["tool_calls_made"] = (final_done.get("tool_calls_made") or 0) + 1
    final_done["meta"] = {
        **(final_done.get("meta") or {}),
        "path": "guard_then_synth",
        "rag_invoked": True,
        "guard_input_tokens": decision.input_tokens,
        "guard_output_tokens": decision.output_tokens,
    }

    logger.info(
        "agent_stream_done",
        session_id=session_id,
        status=final_done.get("status"),
        sources=len(final_done.get("sources") or []),
        tool_calls=final_done.get("tool_calls_made"),
    )
    yield final_done
