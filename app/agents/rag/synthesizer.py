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

import asyncio
import json
import re
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
# Reranker multiplies raw qdrant score by tier_weight (default 0.5 for
# unknown artifact_class), so post-rerank image scores typically land in
# the 0.2-0.5 band. A 0.65 floor was effectively unreachable.
MIN_IMAGE_ATTACH_SCORE = 0.2


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
        ),
        types.FunctionDeclaration(
            name="fetch_document_outline",
            description=(
                "Get the high-level shape of a single ingested document — "
                "filename, page count, headings (with page numbers), table "
                "count, image count, and ingestion timestamp. Use this for "
                "questions about a document's STRUCTURE: 'tell me about "
                "this document', 'what is in document X', 'how long is "
                "this', 'what sections does it have'. Do NOT use for "
                "content-detail questions (use rag_search for those). "
                "Counts against the same 3-call budget as rag_search."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "document_id": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "The UUID of the document to outline. If the "
                            "user is currently scoped to a single document, "
                            "use that one; otherwise use the most relevant "
                            "document_id from prior search results."
                        ),
                    ),
                },
                required=["document_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="fetch_table",
            description=(
                "Return the FULL markdown of a specific table from an "
                "ingested document. rag_search truncates table cells to "
                "~800 chars per result; use this tool when the user asks "
                "to 'show the full table', 'expand that table', 'list "
                "every row', or when you've identified a specific table "
                "(by table_id or page) in prior search results and need "
                "the complete content. Counts against the same 3-call "
                "budget as rag_search."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "document_id": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "UUID of the document containing the table."
                        ),
                    ),
                    "table_id": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "The table_id from a prior search result "
                            "(preferred). Omit if unknown and use page_num."
                        ),
                    ),
                    "page_num": types.Schema(
                        type=types.Type.INTEGER,
                        description=(
                            "0-indexed page number containing the table. "
                            "Used only when table_id is unknown; returns "
                            "all tables on that page."
                        ),
                    ),
                },
                required=["document_id"],
            ),
        ),
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
2. Decide which tool(s) to call. You have a HARD budget of 3 tool calls
   total (any combination of rag_search, fetch_document_outline, and
   fetch_table).
   • Document-shape question ("tell me about this document", "what's in
     it", "how long is this", "what sections does it have") →
     fetch_document_outline FIRST. One call is usually enough; you may
     follow it with one rag_search for narrative if the user wants more.
   • Full-table request ("show me the full NPT table", "every row",
     "expand that table") AFTER you've identified a table_id from a prior
     rag_search result → fetch_table(document_id, table_id).
   • Single-topic content question ("What was NPT for Sajaa 6?") → ONE
     rag_search call.
   • Multi-topic / comparison content question ("Compare NPT and casing
     depth") → up to 3 rag_search calls, one per sub-topic.
   • If your first call already returned strong, on-topic evidence, do NOT
     call again — answer.
3. After 3 tool calls (a hard limit) you will not be allowed any more
   searches; do not pretend you can call a tool again after that.

Final-answer rules (CRITICAL):

- Lead with a one-sentence direct answer that restates the subject.
  • Yes/No → start with "Yes," or "No," + the headline fact.
  • What/Where/How-much → start with the value: "NPT for Sajaa 6 was 9.8% of
    total time and 7.8% of total cost [S1]."
  • List/table → start with "Here is the casing program:" then a Markdown table.
  • Broad open-ended ("tell me about X") → produce a grounded summary from
    whatever evidence you retrieved. NEVER answer "I cannot answer" if you
    have any related sources — give the user the partial summary.
- Cite evidence by mentioning the source DOCUMENT FILENAME inline in
  parentheses, e.g. "NPT was 9.8% of total time (Sajaa_6_EOWR.pdf, p.14)."
  Do NOT use [S1], [S2] tags. Use the filename string verbatim from the
  sources block. Page numbers are 1-based.
- Do NOT call fetch_table speculatively. Only call it when (a) the user
  explicitly asks for the full table, AND (b) you already have a concrete
  table_id or page_num from a prior rag_search hit. If you don't have a
  table_id from search results, do NOT guess one; just answer from the
  truncated chunk.
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


def _dedupe_documents(hits: list[SearchResult]) -> list[dict[str, Any]]:
    """Cheap deduped list of source documents from qdrant payloads.

    Replaces the LLM-based citation verifier. One entry per
    ``document_id`` (or ``filename`` when document_id is missing),
    carrying the filename + the set of page numbers that contributed
    hits + the best score seen.
    """
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in hits:
        key = (r.document_id or r.filename or r.doc_name or "").strip()
        if not key:
            continue
        page = (r.page_num + 1) if isinstance(r.page_num, int) else None
        existing = by_key.get(key)
        if existing is None:
            entry: dict[str, Any] = {
                "document_id": r.document_id,
                "filename": r.filename or r.doc_name,
                "doc_name": r.doc_name,
                "pages": [page] if page is not None else [],
                "best_score": float(r.score),
                "hit_count": 1,
            }
            by_key[key] = entry
            order.append(key)
        else:
            if page is not None and page not in existing["pages"]:
                existing["pages"].append(page)
            if float(r.score) > existing["best_score"]:
                existing["best_score"] = float(r.score)
            existing["hit_count"] += 1
    out: list[dict[str, Any]] = []
    for key in order:
        entry = by_key[key]
        entry["pages"].sort()
        out.append(entry)
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
# Tool dispatch
# ---------------------------------------------------------------------------

async def _dispatch_tool_call(
    *,
    fc: Any,
    calls_made: int,
    embedder: GeminiEmbedder,
    vector_store: QdrantVectorStore,
    storage: SupabaseStorage | None,
    user_id: str,
    project_id: str | None,
    document_id: str | None,
    include_images: bool,
) -> tuple[types.Part, list[SearchResult], bool, str | None]:
    """Run a single function call. Returns (response_part, hits, counted, status_event_query).

    ``counted`` is True iff this call should consume one of the 3 budget slots.
    ``status_event_query`` is set for rag_search so streaming surfaces it.
    """
    name = fc.name or "rag_search"
    args = dict(fc.args or {})

    if name == "fetch_document_outline":
        from app.db.queries.document_outline import fetch_outline

        doc_id = (args.get("document_id") or document_id or "").strip()
        if not doc_id:
            return (
                types.Part.from_function_response(
                    name=name, response={"error": "document_id required"},
                ),
                [], False, None,
            )
        try:
            outline = await fetch_outline(
                document_id=doc_id, user_id=user_id,
                storage=storage, vector_store=vector_store,
            ) if storage is not None else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("synth_outline_failed",
                           document_id=doc_id, error=str(exc))
            outline = None
        if outline is None:
            return (
                types.Part.from_function_response(
                    name=name,
                    response={"error": f"document {doc_id} not found or "
                                       "not owned by caller"},
                ),
                [], True, None,
            )
        logger.info("synth_outline_called", document_id=doc_id,
                    pages=outline.get("page_count"))
        return (
            types.Part.from_function_response(
                name=name,
                response={
                    "calls_remaining": max(0, MAX_RAG_CALLS - calls_made - 1),
                    "outline": outline,
                },
            ),
            [], True, None,
        )

    if name == "fetch_table":
        doc_id = (args.get("document_id") or document_id or "").strip()
        if not doc_id:
            return (
                types.Part.from_function_response(
                    name=name, response={"error": "document_id required"},
                ),
                [], False, None,
            )
        table_id_arg = (args.get("table_id") or "").strip() or None
        page_arg = args.get("page_num")
        try:
            page_num_arg = int(page_arg) if page_arg is not None else None
        except (TypeError, ValueError):
            page_num_arg = None
        if table_id_arg is None and page_num_arg is None:
            return (
                types.Part.from_function_response(
                    name=name,
                    response={"error": "either table_id or page_num required"},
                ),
                [], False, None,
            )
        try:
            tables = await vector_store.fetch_table_chunks(
                user_id=user_id,
                document_id=doc_id,
                table_id=table_id_arg,
                page_num=page_num_arg,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("synth_fetch_table_failed",
                           document_id=doc_id, table_id=table_id_arg,
                           page_num=page_num_arg, error=str(exc))
            tables = []
        # Display pages as 1-indexed for the model (matches user-facing).
        for t in tables:
            pn = t.get("page_num")
            t["page"] = (pn + 1) if isinstance(pn, int) else None
        logger.info("synth_fetch_table_called", document_id=doc_id,
                    table_id=table_id_arg, page_num=page_num_arg,
                    found=len(tables))
        return (
            types.Part.from_function_response(
                name=name,
                response={
                    "calls_remaining": max(0, MAX_RAG_CALLS - calls_made - 1),
                    "tables": tables,
                },
            ),
            [], True, None,
        )

    # Default: rag_search
    sub_query = (args.get("query") or "").strip()
    if not sub_query:
        return (
            types.Part.from_function_response(
                name=name, response={"error": "empty query"},
            ),
            [], False, None,
        )
    hits = await rag_search(
        sub_query,
        embedder=embedder, vector_store=vector_store,
        user_id=user_id, project_id=project_id, document_id=document_id,
        include_images=include_images,
    )
    return (
        types.Part.from_function_response(
            name=name,
            response={
                "calls_remaining": max(0, MAX_RAG_CALLS - calls_made - 1),
                "results": _serialize_for_model(hits),
            },
        ),
        hits, True, sub_query,
    )


# ---------------------------------------------------------------------------
# Follow-up suggestion chips
# ---------------------------------------------------------------------------

MAX_FOLLOWUPS = 3

_FOLLOWUPS_SYSTEM = (
    "You generate at most 3 short follow-up question suggestions a user "
    "might naturally ask next, given the question they just asked and the "
    "answer they just received. Constraints:\n"
    "- Each suggestion is a complete question, max 12 words, no trailing period.\n"
    "- Suggestions must be ANSWERABLE from the same set of ingested "
    "well-operations documents (don't invent topics not implied by the "
    "answer).\n"
    "- Diverse: don't repeat the same topic three times.\n"
    "- If the answer was 'no evidence', suggest reformulations or related "
    "shapes the user could try.\n"
    "OUTPUT RULES (STRICT): Respond with raw JSON only. The first character "
    "of your response MUST be '{'. No prose, no preamble like 'Here is the "
    "JSON', no markdown code fences (no ```), no trailing commentary. "
    "Schema: {\"followups\": [\"...\", \"...\"]}."
)


async def generate_followups(
    *,
    client: genai.Client,
    settings: Settings,
    user_message: str,
    answer: str,
    has_sources: bool,
) -> list[str]:
    """Best-effort follow-up suggestions. Returns ``[]`` on any failure."""
    if not (answer or "").strip():
        return []
    user_block = (
        f"User question:\n{user_message}\n\n"
        f"Assistant answer:\n{answer}\n\n"
        f"Answer was grounded in {'one or more' if has_sources else 'NO'} "
        f"retrieved sources."
    )
    try:
        resp = await client.aio.models.generate_content(
            model=settings.chat_synth_model,
            contents=[types.Content(role="user",
                                    parts=[types.Part.from_text(text=user_block)])],
            config=types.GenerateContentConfig(
                system_instruction=_FOLLOWUPS_SYSTEM,
                temperature=0.4,
                max_output_tokens=1024,
                response_mime_type="application/json",
                # NOTE: do NOT set thinking_budget=0 here. Gemini 3 Flash
                # combined with response_mime_type=application/json plus a
                # zero thinking budget intermittently returns an empty
                # body (no plan -> no JSON), which manifests as "missing
                # followups" on the FE. Letting the model think briefly
                # (default budget) restores reliability. This call runs
                # in the async post-`done` window so the extra ~1-2 s is
                # invisible to the user.
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("synth_followups_error", error=str(exc),
                       error_type=type(exc).__name__)
        return []

    raw = (getattr(resp, "text", "") or "").strip()
    finish_reason = None
    try:
        finish_reason = str(resp.candidates[0].finish_reason)
    except (AttributeError, IndexError, TypeError):
        pass
    if not raw:
        logger.debug("synth_followups_empty", finish_reason=finish_reason)
        return []
    # Models occasionally ignore response_mime_type=json and prefix with prose
    # ("Here is the JSON requested:") or wrap in ```json fences. Find the
    # first { or [ and use raw_decode so trailing fences / extra data don't
    # blow up the parse. Also strip trailing commas before } or ].
    cleaned = raw
    start = min(
        (i for i in (cleaned.find("{"), cleaned.find("[")) if i >= 0),
        default=-1,
    )
    if start < 0:
        logger.debug("synth_followups_bad_json",
                     preview=raw[:200], length=len(raw),
                     finish_reason=finish_reason)
        return []
    candidate = re.sub(r",(\s*[\]}])", r"\1", cleaned[start:])
    try:
        data, _ = json.JSONDecoder().raw_decode(candidate)
    except json.JSONDecodeError:
        logger.debug("synth_followups_bad_json",
                     preview=raw[:200], length=len(raw),
                     finish_reason=finish_reason)
        return []
    items = data.get("followups") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        text = item.strip().rstrip(".?!")
        if not text:
            continue
        if text in out:
            continue
        out.append(text)
        if len(out) >= MAX_FOLLOWUPS:
            break
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
        max_output_tokens=32000,
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
                    response={"error": "tool budget exhausted; produce the "
                                       "final answer from prior results"},
                ))
                continue
            part, hits, counted, _ = await _dispatch_tool_call(
                fc=fc, calls_made=calls_made,
                embedder=embedder, vector_store=vector_store, storage=storage,
                user_id=user_id, project_id=project_id, document_id=document_id,
                include_images=include_images,
            )
            if counted:
                calls_made += 1
            if hits:
                all_hits = _dedupe_merge(all_hits, hits)
            response_parts.append(part)

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
                    max_output_tokens=32000,
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

    # Verifier removed — see streaming path. Followups still run.
    try:
        followups = await generate_followups(
            client=client, settings=settings,
            user_message=user_message, answer=final_text,
            has_sources=bool(all_hits),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("synth_with_tools_followups_error", error=str(exc))
        followups = []
    verification_meta = {"skipped": True}

    documents_used = _dedupe_documents(top_hits)

    return AgentResponse(
        status=status,
        answer=final_text,
        followups=followups,
        sources=sources,
        evidence=[],  # evidence cards retired; UI uses sources directly
        attached_pages=attached_pages,
        tool_calls_made=calls_made,
        meta={
            "rag_calls": calls_made,
            "include_images": include_images,
            "total_hits": len(all_hits),
            "verification": verification_meta,
            "documents_used": documents_used,
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
        max_output_tokens=32000,
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
                    response={"error": "tool budget exhausted; produce the "
                                       "final answer from prior results"},
                ))
                continue
            # Surface a status frame before the call so the FE can show
            # "Searching documents…" / "Reading outline…".
            tool_name = fc.name or "rag_search"
            args_preview = dict(fc.args or {})
            if tool_name == "rag_search":
                preview = (args_preview.get("query") or "").strip()
                if preview:
                    yield {"event": "status", "phase": "rag_search",
                           "query": preview}
            elif tool_name == "fetch_document_outline":
                yield {"event": "status", "phase": "fetch_document_outline"}
            elif tool_name == "fetch_table":
                yield {"event": "status", "phase": "fetch_table"}

            part, hits, counted, _ = await _dispatch_tool_call(
                fc=fc, calls_made=calls_made,
                embedder=embedder, vector_store=vector_store, storage=storage,
                user_id=user_id, project_id=project_id, document_id=document_id,
                include_images=include_images,
            )
            if counted:
                calls_made += 1
            if hits:
                all_hits = _dedupe_merge(all_hits, hits)
            response_parts.append(part)

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
                max_output_tokens=32000,
                thinking_config=types.ThinkingConfig(include_thoughts=True),
            ),
        )
        async for chunk in stream:
            # Gemini emits thought summaries and answer text as separate
            # Parts on the same candidate, distinguished by part.thought.
            # Walk parts manually so we can route them to different SSE
            # events (`thought` for engagement during the synth wait;
            # `token` for the actual answer the user reads).
            try:
                parts = chunk.candidates[0].content.parts or []
            except (AttributeError, IndexError, TypeError):
                parts = []
            for part in parts:
                text = getattr(part, "text", "") or ""
                if not text:
                    continue
                if getattr(part, "thought", False):
                    yield {"event": "thought", "text": text}
                else:
                    final_text_parts.append(text)
                    yield {"event": "token", "text": text}
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

    # Run followups + verifier concurrently — both are independent of each
    # other (followups needs only the answer text; verifier needs answer +
    # sources). Sequential wait was a flat ~5-40s tax on every turn.
    empty_response = not (final_text or "").strip()
    if empty_response:
        logger.warning(
            "synth_stream_empty_answer",
            status=status, calls=calls_made, hits=len(all_hits),
        )

    # Cheap deduped list of source documents straight from the qdrant
    # payloads — no LLM call. Replaces the LLM-based citation verifier.
    documents_used = _dedupe_documents(top_hits)

    common_done_fields = {
        "session_id": None,  # filled in by route layer
        "status": status,
        "answer": final_text,
        "sources": sources,
        "documents_used": documents_used,
        "attached_pages": attached_pages,
        "tool_calls_made": calls_made,
    }

    # ------------------------------------------------------------------
    # Progressive finalisation. Emit `done` IMMEDIATELY after the last
    # token. The verifier was removed (was costing ~20s/turn at timeout
    # for negligible quality gain — the model now cites by document
    # filename inline and the deduped documents_used list above gives
    # the FE everything it needs for source attribution). Followups
    # still run async so they don't block the answer.
    # ------------------------------------------------------------------
    yield {
        "event": "done",
        **common_done_fields,
        "answer": final_text,
        "followups": {"pending": True},
        "meta": {
            "rag_calls": calls_made,
            "include_images": include_images,
            "total_hits": len(all_hits),
            "verification": {"skipped": True},
            "empty_response": empty_response,
        },
    }

    # Verification is permanently skipped. Emit the frame immediately so
    # the FE protocol stays uniform (it can still hide the spinner on
    # this signal).
    yield {
        "event": "verification_update",
        "verification": {"skipped": True},
    }

    try:
        followups = await generate_followups(
            client=client, settings=settings,
            user_message=user_message, answer=final_text,
            has_sources=bool(all_hits),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("synth_stream_followups_error", error=str(exc))
        followups = []

    yield {
        "event": "followups_update",
        "followups": followups,
    }
