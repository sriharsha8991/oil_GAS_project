"""Internal synthesizer — Flash + function-calling on a single ``rag_search`` tool.

Behavior:
  • Receives the resolved query from the guard (and the raw user message + history).
  • Plans whether to decompose. Each decomposition step is one rag_search call.
  • May invoke ``rag_search`` AT MOST 3 times per turn. After that, the model
    is forced to answer from what it has.
  • Returns a grounded answer + the top 10 sources actually surfaced.
  • Page-image attachments are produced ONLY when the user explicitly asked
    about visuals (image / figure / diagram / chart / etc.).
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from google import genai
from google.genai import types

from app.agents.rag.evidence import build_sources
from app.agents.rag.retriever import rag_search, wants_images
from app.agents.rag.types import AgentResponse
from app.agents.rag_tools import SearchResult
from app.config import Settings
from app.storage.supabase_storage import SupabaseStorage
from app.utils.logging import get_logger
from app.vectorstore.embedder import GeminiEmbedder
from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)

MAX_RAG_CALLS = 3
MAX_SOURCES_RETURNED = 10
MAX_INLINE_IMAGES = 2
MIN_IMAGE_ATTACH_SCORE = 0.65


# ---------------------------------------------------------------------------
# Tool declaration
# ---------------------------------------------------------------------------

_RAG_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="rag_search",
            description=(
                "Search the user's ingested well-operations documents for "
                "evidence relevant to a focused sub-query. Returns a list of "
                "sources (text chunks, table rows, named entities, and "
                "optionally image descriptions). Call this 1 to 3 times per "
                "user turn — once for a focused question, two or three times "
                "to decompose a multi-part question. Only call again when "
                "the previous results clearly missed a sub-topic."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "A focused search query — one specific "
                            "sub-question or topic, in natural language."
                        ),
                    ),
                },
                required=["query"],
            ),
        )
    ]
)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYNTH_SYSTEM = """\
You are WellSynth's internal answer crafter. The guard has already routed
this turn to you because it requires evidence from ingested documents.

Workflow on every turn:

1. Read the resolved query and the conversation history.
2. Decide whether to decompose the question:
   • Single-topic question (e.g. "What was NPT for Sajaa 6?") → ONE rag_search call.
   • Multi-topic / comparison question (e.g. "Compare NPT and casing depth")
     → up to 3 rag_search calls, one per sub-topic.
   • If your first call already returned strong, on-topic evidence, do NOT
     call again — answer.
3. After at most 3 rag_search calls (a hard limit), produce the final answer.
   You will not be allowed any more searches; do not pretend you can call
   the tool again after that.

Final-answer rules (CRITICAL):

- Lead with a one-sentence direct answer that restates the subject.
  • Yes/No → start with "Yes," or "No," + the headline fact.
  • What/Where/How-much → start with the value: "NPT for Sajaa 6 was 9.8% of
    total time and 7.8% of total cost [S1]."
  • List/table → start with "Here is the casing program:" then a Markdown table.
  • Broad open-ended ("tell me about X") → produce a grounded summary from
    whatever evidence you retrieved. NEVER answer "I cannot answer" if you
    have any related sources — give the user the partial summary.
- Cite every numeric value, depth, formation, equipment, or named entity
  with a [Sn] tag matching the source list provided to you.
- Use Markdown tables when the answer is naturally tabular.
- If parts of the question are unanswered by the evidence, acknowledge it
  briefly in the answer text — do not fabricate.
- Stay direct and professional. No filler ("Certainly!", "Of course,").

Output: plain Markdown text. NO JSON envelope. NO code fences around the
answer. Just the user-facing answer.
"""


_NO_EVIDENCE_TAIL = (
    "\n\nNote to the answerer: rag_search returned no usable hits for any "
    "of your queries. Tell the user briefly that you couldn't find evidence "
    "in the ingested documents for this specific question, summarise (in 1-3 "
    "bullet points) the closest related content that DID come back if any, "
    "and suggest 2-3 follow-up questions they might actually mean. Keep it "
    "under 120 words."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize_for_model(results: list[SearchResult], max_items: int = 12) -> list[dict[str, Any]]:
    """Compact serialization sent back to the LLM as the tool result.

    Keeps just what the model needs to ground an answer; trims long
    fields aggressively to control token cost.
    """
    out: list[dict[str, Any]] = []
    for r in results[:max_items]:
        page = (r.page_num + 1) if isinstance(r.page_num, int) else None
        text = (r.text or "")[:600]
        item: dict[str, Any] = {
            "score": round(r.score, 3),
            "doc_name": r.doc_name,
            "page": page,
            "kind": r.kind or r.collection.removeprefix("wellsynthai_"),
        }
        if text:
            item["text"] = text
        if r.canonical_value:
            item["entity"] = {
                "type": r.entity_type,
                "value": r.canonical_value,
                "unit": r.unit,
            }
        if r.table_markdown:
            item["table"] = r.table_markdown[:800]
        if r.image_type or r.description:
            item["image"] = {
                "type": r.image_type,
                "description": (r.description or "")[:200],
            }
        out.append(item)
    return out


def _dedupe_merge(
    existing: list[SearchResult], new_hits: list[SearchResult]
) -> list[SearchResult]:
    seen: set[tuple] = set()
    for r in existing:
        seen.add((
            r.collection, r.doc_name, r.page_num, r.chunk_index,
            r.table_id, r.canonical_id,
            tuple(r.bbox) if r.bbox else None,
        ))
    out = list(existing)
    for r in new_hits:
        key = (
            r.collection, r.doc_name, r.page_num, r.chunk_index,
            r.table_id, r.canonical_id,
            tuple(r.bbox) if r.bbox else None,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _build_sources_prompt(sources: list[dict[str, Any]]) -> str:
    """Render the sources list as a tagged block the model can cite."""
    lines: list[str] = []
    for s in sources:
        sid = s.get("source_id")
        coll = s.get("collection", "")
        page = s.get("page_num")
        page_str = f"p{page + 1}" if isinstance(page, int) else "?"
        doc = s.get("doc_name") or s.get("filename") or ""
        head = f"[{sid}] ({coll} • {doc} • {page_str})"

        if s.get("kind") == "table" and s.get("table_markdown"):
            body = s["table_markdown"]
        elif s.get("collection") == "wellsynthai_entities":
            body = (
                f"{s.get('entity_type','')}: "
                f"{s.get('canonical_value','')} {s.get('unit','') or ''}".strip()
            )
            if s.get("text"):
                body += f"\n  context: {s['text']}"
        elif s.get("collection") == "wellsynthai_images":
            body = (
                f"{s.get('image_type','')}: {s.get('description','')}"
            ).strip()
        else:
            body = s.get("text", "")
        lines.append(f"{head}\n{body}")
    return "\n\n".join(lines)


async def _load_page_pngs(
    *,
    storage: SupabaseStorage,
    image_hits: list[SearchResult],
    fallback_user_id: str,
    fallback_project_id: str | None,
    fallback_document_id: str | None,
) -> list[dict[str, Any]]:
    """Materialize up to MAX_INLINE_IMAGES page PNGs as attached_pages records."""
    out: list[dict[str, Any]] = []
    seen_pages: set[tuple[str, int]] = set()
    for hit in image_hits:
        if len(out) >= MAX_INLINE_IMAGES:
            break
        if hit.page_num is None or not hit.doc_name:
            continue
        if hit.score < MIN_IMAGE_ATTACH_SCORE:
            continue
        key = (hit.doc_name, hit.page_num)
        if key in seen_pages:
            continue
        seen_pages.add(key)
        try:
            png = await storage.read_page_png(
                hit.user_id or fallback_user_id,
                hit.project_id or fallback_project_id,
                hit.document_id or fallback_document_id,
                hit.page_num,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("attach_image_load_failed", error=str(exc),
                           doc=hit.doc_name, page=hit.page_num)
            continue
        if not png:
            continue
        out.append({
            "doc_name": hit.doc_name,
            "page_num": hit.page_num,
            "document_id": hit.document_id,
            "image_type": hit.image_type,
            "description": hit.description,
            "size_bytes": len(png),
            "url": (
                f"/documents/{hit.document_id}/pages/{hit.page_num}/image"
                if hit.document_id else None
            ),
        })
    return out


# ---------------------------------------------------------------------------
# Synthesize
# ---------------------------------------------------------------------------

async def synthesize(
    *,
    client: genai.Client,
    settings: Settings,
    user_message: str,
    resolved_query: str,
    history_text: str,
    embedder: GeminiEmbedder,
    vector_store: QdrantVectorStore,
    user_id: str,
    project_id: str | None = None,
    document_id: str | None = None,
    storage: SupabaseStorage | None = None,
) -> AgentResponse:
    """Run the function-calling synthesizer loop and return the final response."""

    include_images = wants_images(user_message)

    history_block = (
        f"Conversation so far (resolve coreferences against this; do NOT "
        f"cite history as a source):\n{history_text}\n\n"
        if history_text else ""
    )
    initial_user_text = (
        f"{history_block}"
        f"User's literal message:\n{user_message}\n\n"
        f"Resolved question (answer THIS):\n{resolved_query}\n\n"
        f"You may call rag_search up to {MAX_RAG_CALLS} times. Begin by "
        f"deciding whether to decompose, then call rag_search."
    )

    contents: list[types.Content] = [
        types.Content(role="user",
                      parts=[types.Part.from_text(text=initial_user_text)])
    ]

    config_with_tools = types.GenerateContentConfig(
        system_instruction=_SYNTH_SYSTEM,
        temperature=0.2,
        max_output_tokens=2048,
        tools=[_RAG_TOOL],
    )

    calls_made = 0
    all_hits: list[SearchResult] = []
    final_text = ""

    # ------------------------------------------------------------------
    # Function-calling loop (max MAX_RAG_CALLS rag_search invocations)
    # ------------------------------------------------------------------
    while True:
        try:
            resp = await client.aio.models.generate_content(
                model=settings.chat_synth_model,
                contents=contents,
                config=config_with_tools,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("synth_llm_error", error=str(exc),
                           error_type=type(exc).__name__)
            break

        candidate = (resp.candidates or [None])[0]
        parts = (
            getattr(candidate.content, "parts", None) or []
            if candidate and candidate.content else []
        )

        function_calls = [
            getattr(p, "function_call", None) for p in parts
            if getattr(p, "function_call", None)
        ]

        # No tool calls → model wants to emit final text.
        if not function_calls:
            final_text = (getattr(resp, "text", "") or "").strip()
            break

        # Append the model's tool-call turn so the SDK can match the
        # function_response back to it.
        contents.append(candidate.content)

        # Execute each requested call (still respecting the global cap).
        response_parts: list[types.Part] = []
        for fc in function_calls:
            if calls_made >= MAX_RAG_CALLS:
                logger.info("synth_rag_cap_reached", calls=calls_made)
                response_parts.append(types.Part.from_function_response(
                    name=fc.name or "rag_search",
                    response={"error": "search budget exhausted; produce the "
                                       "final answer from prior results"},
                ))
                continue
            args = dict(fc.args or {})
            sub_query = (args.get("query") or "").strip()
            if not sub_query:
                response_parts.append(types.Part.from_function_response(
                    name=fc.name or "rag_search",
                    response={"error": "empty query"},
                ))
                continue
            hits = await rag_search(
                sub_query,
                embedder=embedder, vector_store=vector_store,
                user_id=user_id, project_id=project_id, document_id=document_id,
                include_images=include_images,
            )
            calls_made += 1
            all_hits = _dedupe_merge(all_hits, hits)
            response_parts.append(types.Part.from_function_response(
                name=fc.name or "rag_search",
                response={
                    "calls_remaining": max(0, MAX_RAG_CALLS - calls_made),
                    "results": _serialize_for_model(hits),
                },
            ))

        contents.append(types.Content(role="user", parts=response_parts))

        # Hard cap — once budget is exhausted, force the next call to be
        # a final answer with no more tools.
        if calls_made >= MAX_RAG_CALLS:
            break

    # ------------------------------------------------------------------
    # If the loop exited without final text, force a no-tool answer call.
    # ------------------------------------------------------------------
    if not final_text:
        all_hits.sort(key=lambda h: h.score, reverse=True)
        top_for_grounding = all_hits[:MAX_SOURCES_RETURNED]
        sources_for_prompt = build_sources(top_for_grounding)
        empty_tail = "" if all_hits else _NO_EVIDENCE_TAIL
        final_user_text = (
            f"You have used your search budget ({calls_made}/{MAX_RAG_CALLS} "
            f"calls). Produce the final answer now using only the sources "
            f"below; do NOT call any tools.\n\n"
            f"User's literal message:\n{user_message}\n\n"
            f"Resolved question:\n{resolved_query}\n\n"
            f"Sources:\n{_build_sources_prompt(sources_for_prompt) or '(none)'}"
        )
        try:
            resp = await client.aio.models.generate_content(
                model=settings.chat_synth_model,
                contents=[types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=final_user_text)],
                )],
                config=types.GenerateContentConfig(
                    system_instruction=_SYNTH_SYSTEM + empty_tail,
                    temperature=0.2,
                    max_output_tokens=2048,
                ),
            )
            final_text = (getattr(resp, "text", "") or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("synth_final_call_error", error=str(exc))
            final_text = ""

    # ------------------------------------------------------------------
    # Top-10 sources only, plus optional image attachments.
    # ------------------------------------------------------------------
    all_hits.sort(key=lambda h: h.score, reverse=True)
    top_hits = all_hits[:MAX_SOURCES_RETURNED]
    sources = build_sources(top_hits)

    attached_pages: list[dict[str, Any]] = []
    if include_images and storage is not None:
        image_hits = [h for h in all_hits if h.collection == "wellsynthai_images"]
        attached_pages = await _load_page_pngs(
            storage=storage, image_hits=image_hits,
            fallback_user_id=user_id, fallback_project_id=project_id,
            fallback_document_id=document_id,
        )

    # Last-resort deterministic message so the user never sees blank.
    if not final_text:
        if all_hits:
            final_text = (
                "I retrieved related content but couldn't compose a confident "
                "answer. The sources below show the closest material I found."
            )
        else:
            final_text = (
                "I couldn't find evidence in the ingested documents for this "
                "question. Try rephrasing, narrowing to a specific document, "
                "or uploading a more relevant PDF."
            )
        status = "no_evidence"
    else:
        status = "answered" if all_hits else "no_evidence"

    return AgentResponse(
        status=status,
        answer=final_text,
        sources=sources,
        evidence=[],  # evidence cards retired; UI uses sources directly
        attached_pages=attached_pages,
        tool_calls_made=calls_made,
        meta={
            "rag_calls": calls_made,
            "include_images": include_images,
            "total_hits": len(all_hits),
        },
    )


# ---------------------------------------------------------------------------
# Streaming variant — emits status / token / done events for SSE.
# ---------------------------------------------------------------------------

async def synthesize_stream(
    *,
    client: genai.Client,
    settings: Settings,
    user_message: str,
    resolved_query: str,
    history_text: str,
    embedder: GeminiEmbedder,
    vector_store: QdrantVectorStore,
    user_id: str,
    project_id: str | None = None,
    document_id: str | None = None,
    storage: SupabaseStorage | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Async generator yielding ``status`` / ``token`` / ``done`` events.

    The function-calling loop (rag_search dispatch) is non-streaming — those
    turns are short and we need their full payload to dispatch tools. The
    final answer turn is streamed token-by-token.
    """

    include_images = wants_images(user_message)

    history_block = (
        f"Conversation so far (resolve coreferences against this; do NOT "
        f"cite history as a source):\n{history_text}\n\n"
        if history_text else ""
    )
    initial_user_text = (
        f"{history_block}"
        f"User's literal message:\n{user_message}\n\n"
        f"Resolved question (answer THIS):\n{resolved_query}\n\n"
        f"You may call rag_search up to {MAX_RAG_CALLS} times. Begin by "
        f"deciding whether to decompose, then call rag_search."
    )

    contents: list[types.Content] = [
        types.Content(role="user",
                      parts=[types.Part.from_text(text=initial_user_text)])
    ]

    config_with_tools = types.GenerateContentConfig(
        system_instruction=_SYNTH_SYSTEM,
        temperature=0.2,
        max_output_tokens=2048,
        tools=[_RAG_TOOL],
    )

    calls_made = 0
    all_hits: list[SearchResult] = []

    # ------------------------------------------------------------------
    # Tool-call loop. We don't stream tokens during this phase — we only
    # surface status events so the FE can show "Searching documents…".
    # ------------------------------------------------------------------
    while True:
        try:
            resp = await client.aio.models.generate_content(
                model=settings.chat_synth_model,
                contents=contents,
                config=config_with_tools,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("synth_stream_llm_error", error=str(exc),
                           error_type=type(exc).__name__)
            break

        candidate = (resp.candidates or [None])[0]
        parts = (
            getattr(candidate.content, "parts", None) or []
            if candidate and candidate.content else []
        )
        function_calls = [
            getattr(p, "function_call", None) for p in parts
            if getattr(p, "function_call", None)
        ]

        if not function_calls:
            # Model wants to answer. Discard its non-streamed text — we
            # will redo this turn with streaming below.
            break

        contents.append(candidate.content)

        response_parts: list[types.Part] = []
        for fc in function_calls:
            if calls_made >= MAX_RAG_CALLS:
                logger.info("synth_stream_rag_cap_reached", calls=calls_made)
                response_parts.append(types.Part.from_function_response(
                    name=fc.name or "rag_search",
                    response={"error": "search budget exhausted; produce the "
                                       "final answer from prior results"},
                ))
                continue
            args = dict(fc.args or {})
            sub_query = (args.get("query") or "").strip()
            if not sub_query:
                response_parts.append(types.Part.from_function_response(
                    name=fc.name or "rag_search",
                    response={"error": "empty query"},
                ))
                continue
            yield {"event": "status", "phase": "rag_search", "query": sub_query}
            hits = await rag_search(
                sub_query,
                embedder=embedder, vector_store=vector_store,
                user_id=user_id, project_id=project_id, document_id=document_id,
                include_images=include_images,
            )
            calls_made += 1
            all_hits = _dedupe_merge(all_hits, hits)
            response_parts.append(types.Part.from_function_response(
                name=fc.name or "rag_search",
                response={
                    "calls_remaining": max(0, MAX_RAG_CALLS - calls_made),
                    "results": _serialize_for_model(hits),
                },
            ))

        contents.append(types.Content(role="user", parts=response_parts))

        if calls_made >= MAX_RAG_CALLS:
            break

    # ------------------------------------------------------------------
    # Final streaming turn — no tools, the model must answer now.
    # ------------------------------------------------------------------
    yield {"event": "status", "phase": "synthesizing"}

    all_hits.sort(key=lambda h: h.score, reverse=True)
    top_for_grounding = all_hits[:MAX_SOURCES_RETURNED]
    sources_for_prompt = build_sources(top_for_grounding)
    empty_tail = "" if all_hits else _NO_EVIDENCE_TAIL
    final_user_text = (
        f"You have used {calls_made}/{MAX_RAG_CALLS} search calls. "
        f"Produce the final answer now using only the sources below; "
        f"do NOT call any tools.\n\n"
        f"User's literal message:\n{user_message}\n\n"
        f"Resolved question:\n{resolved_query}\n\n"
        f"Sources:\n{_build_sources_prompt(sources_for_prompt) or '(none)'}"
    )

    final_text_parts: list[str] = []
    try:
        stream = await client.aio.models.generate_content_stream(
            model=settings.chat_synth_model,
            contents=[types.Content(
                role="user",
                parts=[types.Part.from_text(text=final_user_text)],
            )],
            config=types.GenerateContentConfig(
                system_instruction=_SYNTH_SYSTEM + empty_tail,
                temperature=0.2,
                max_output_tokens=2048,
            ),
        )
        async for chunk in stream:
            piece = getattr(chunk, "text", "") or ""
            if piece:
                final_text_parts.append(piece)
                yield {"event": "token", "text": piece}
    except Exception as exc:  # noqa: BLE001
        logger.warning("synth_stream_final_error", error=str(exc))

    final_text = "".join(final_text_parts).strip()

    # ------------------------------------------------------------------
    # Build sources / attachments / done payload.
    # ------------------------------------------------------------------
    top_hits = all_hits[:MAX_SOURCES_RETURNED]
    sources = build_sources(top_hits)

    attached_pages: list[dict[str, Any]] = []
    if include_images and storage is not None:
        image_hits = [h for h in all_hits if h.collection == "wellsynthai_images"]
        attached_pages = await _load_page_pngs(
            storage=storage, image_hits=image_hits,
            fallback_user_id=user_id, fallback_project_id=project_id,
            fallback_document_id=document_id,
        )

    if not final_text:
        if all_hits:
            final_text = (
                "I retrieved related content but couldn't compose a confident "
                "answer. The sources below show the closest material I found."
            )
        else:
            final_text = (
                "I couldn't find evidence in the ingested documents for this "
                "question. Try rephrasing, narrowing to a specific document, "
                "or uploading a more relevant PDF."
            )
        # Emit the fallback as a single token frame so the FE still sees text.
        yield {"event": "token", "text": final_text}
        status = "no_evidence"
    else:
        status = "answered" if all_hits else "no_evidence"

    yield {
        "event": "done",
        "status": status,
        "answer": final_text,
        "sources": sources,
        "attached_pages": attached_pages,
        "tool_calls_made": calls_made,
        "meta": {
            "rag_calls": calls_made,
            "include_images": include_images,
            "total_hits": len(all_hits),
        },
    }
