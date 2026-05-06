"""Synthesizer — final-answer Flash call with multimodal grounding + JSON envelope."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path  # noqa: F401  (kept for backwards compatibility with imports)
from typing import Any

from google import genai
from google.genai import types

from app.agents.rag.evidence import build_evidence, build_sources
from app.agents.rag.types import (
    AgentResponse,
    Bundle,
    Claim,
    QueryPlan,
    RetrievalAssessment,
    Unanswered,
)
from app.agents.rag.validator import validate_claims
from app.config import Settings
from app.storage.supabase_storage import SupabaseStorage
from app.utils.logging import get_logger

logger = get_logger(__name__)

MAX_INLINE_IMAGES = 4
# Minimum retrieval score required to attach a page PNG to the synthesizer
# call. Below this the image is almost always off-topic and steers the model
# toward unrelated figures, so we skip the upload entirely.
MIN_IMAGE_ATTACH_SCORE = 0.45

_SYNTH_SYSTEM = """\
You are WellSynth AI synthesizing a grounded answer from retrieved evidence.

You will receive:
  • The user's original question AND a resolved version of it (with
    coreferences like "that one", "the deeper one" already resolved against
    the conversation history). Treat the RESOLVED question as the user's
    true intent — that is what you must answer.
  • The conversation history (only when this is a follow-up turn).
  • The query plan (intent, required_facets).
  • Retrieved sources, each tagged with a stable [Sn] id.
  • Possibly attached page images (multimodal).

Output ONLY a JSON object — no prose around it — matching this schema:

{
  "answer": "Markdown-formatted answer. Use tables for tabular intents. Cite each fact with [Sn].",
  "claims": [
    {"text": "atomic factual claim from the answer", "source_id": "S3", "confidence": "high|medium|low"}
  ],
  "unanswered": [
    {"facet": "facet_name", "reason": "why the evidence does not cover this"}
  ],
  "followup_suggestions": ["optional next-step questions for the user"]
}

Answer-shape rules (CRITICAL — this is what makes the answer FEEL right):
  - LEAD with a one-sentence direct answer that restates the subject of the
    resolved question. Do not start with throat-clearing ("Based on the
    sources..."), do not start with the supporting facts.
      • Yes/No question → start with "Yes," or "No," and the headline fact.
      • What/Where/How-much → start with the value: "The 9-5/8\\" casing was
        set at 3,450 ft MD [S3]."
      • List/table → start with "Here is the casing program for Well X:" then
        a Markdown table.
  - Then provide the supporting detail in 1-3 short paragraphs OR a Markdown
    table when the intent is tabular.
  - Stay within the SCOPE the resolved question asked for. Do not add
    unrequested context unless it is necessary to make the headline answer
    safe to use (e.g. unit, datum, well/run identifier).
  - For follow-ups, EXTEND the previous assistant answer instead of
    starting over. Reuse the same subject, units, and depth datum.
    If the user asks "why?" or "more details", drill DEEPER on the same
    topic; do not change subject.
  - When the user is comparing two things, return a small Markdown
    comparison table.

Grounding rules:
  - NEVER invent facts not present in the sources. If evidence is missing for
    a required_facet, list it under `unanswered` instead of guessing.
  - Every numeric value, unit, depth, pressure, formation name, well id, or
    casing size in `answer` MUST trace to a [Sn] cited in `claims`.
  - When tables are in the sources, prefer copying the relevant rows into a
    Markdown table in `answer`.
  - When images are attached, describe what you actually see and reference
    the page number.

Tone:
  - Helpful, direct, professional. No filler ("Certainly!", "Of course,").
  - When parts of the question are unanswered, acknowledge it plainly in
    `answer` (e.g. "I couldn't find ... but here's what is in the document")
    and use `followup_suggestions` to offer 1-3 concrete next questions.
  - If the question is genuinely unanswerable from the evidence, return:
        {"answer": null, "claims": [], "unanswered": [...], "followup_suggestions": [...]}
"""


_NO_EVIDENCE_SYSTEM = """\
You are WellSynth AI. The user's question CANNOT be answered from the
ingested documents — retrieval came back too weak (low relevance scores
and no facet coverage).

Your job is NOT to apologize or refuse coldly. Your job is to:

  1. Tell the user clearly and briefly that you couldn't find a confident
     answer to their specific question in the ingested documents.
  2. Summarize, in 1-3 short bullet points, what RELATED content IS available
     (closest topics, document names, page numbers) — derived from the weak
     hits provided. Do NOT invent topics.
  3. Suggest 2-3 concrete follow-up questions the user might actually mean,
     phrased so they can be clicked / asked next.

Output ONLY a JSON object matching this schema:
{
  "answer": "Markdown message: 1 short paragraph, then a 'Related content I do have:' bullet list, then 'You might want to ask:' bullets. Friendly, concise.",
  "followup_suggestions": ["Question 1", "Question 2", "Question 3"]
}

Constraints:
  - Never claim a fact the weak hits don't support.
  - If the weak hits are completely off-topic, say so honestly and skip the
    bullet list of related content.
  - Keep the message under ~120 words.
"""


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _format_sources_for_prompt(sources: list[dict[str, Any]]) -> str:
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


def _parse_envelope(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = _JSON_BLOCK_RE.search(cleaned)
        if not m:
            return {}
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}


async def _load_page_png(
    storage: SupabaseStorage,
    hit_user_id: str | None,
    hit_project_id: str | None,
    hit_document_id: str | None,
    page_num: int,
) -> bytes | None:
    """Resolve a page PNG via Supabase Storage using the hit's tenant tags."""
    if not hit_user_id or not hit_project_id or not hit_document_id:
        return None
    try:
        return await storage.read_page_png(
            hit_user_id, hit_project_id, hit_document_id, page_num
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "page_png_load_failed",
            user_id=hit_user_id,
            document_id=hit_document_id,
            page_num=page_num,
            error=str(exc),
        )
        return None


async def synthesize(
    *,
    client: genai.Client,
    settings: Settings,
    storage: SupabaseStorage,
    user_message: str,
    plan: QueryPlan,
    bundle: Bundle,
    assessment: RetrievalAssessment,
    history_text: str = "",
    user_id: str,
    project_id: str | None = None,
    document_id: str | None = None,
) -> AgentResponse:
    """Run the synthesizer, validate, and return a structured AgentResponse."""
    sources = build_sources(bundle)

    plan_summary = {
        "intent": plan.intent,
        "required_facets": plan.required_facets,
        "covered_facets": assessment.covered_facets,
        "missing_facets": assessment.missing_facets,
    }
    history_block = (
        f"Conversation so far (use to resolve coreferences like 'that one',\n"
        f"'the next', 'compared to before' — do NOT cite history as a source):\n"
        f"{history_text}\n\n"
        if history_text else ""
    )
    resolved = (plan.resolved_question or "").strip()
    if resolved and resolved != user_message.strip():
        question_block = (
            f"User's literal message:\n{user_message}\n\n"
            f"Resolved question (answer THIS — coreferences resolved against "
            f"history):\n{resolved}\n\n"
        )
    else:
        question_block = f"User question:\n{user_message}\n\n"

    user_block = (
        f"{history_block}"
        f"{question_block}"
        f"Query plan:\n{json.dumps(plan_summary, indent=2)}\n\n"
        f"Retrieved sources ({len(sources)}):\n\n"
        f"{_format_sources_for_prompt(sources)}\n\n"
        "Emit the JSON envelope now."
    )

    parts: list[types.Part] = [types.Part.from_text(text=user_block)]

    # Attach top page PNGs for image hits (multimodal grounding).
    attached_pages: list[dict[str, Any]] = []
    if plan.use_images and bundle.images:
        seen_pages: set[tuple[str, int]] = set()
        for hit in bundle.images:
            if len(attached_pages) >= MAX_INLINE_IMAGES:
                break
            if not hit.doc_name or hit.page_num is None:
                continue
            # Score gate: skip low-confidence image hits — attaching a
            # PNG of an unrelated figure pushes the model toward it.
            if hit.score < MIN_IMAGE_ATTACH_SCORE:
                continue
            key = (hit.doc_name, hit.page_num)
            if key in seen_pages:
                continue
            seen_pages.add(key)
            png = await _load_page_png(
                storage,
                hit.user_id or user_id,
                hit.project_id or project_id,
                hit.document_id or document_id,
                hit.page_num,
            )
            if not png:
                logger.warning("synth_page_png_missing",
                               doc_name=hit.doc_name, page_num=hit.page_num)
                continue
            parts.append(types.Part.from_text(
                text=f"[Attached image: {hit.doc_name} page {hit.page_num + 1} — "
                     f"{hit.image_type or 'figure'}]"
            ))
            parts.append(types.Part.from_bytes(data=png, mime_type="image/png"))
            attached_pages.append({
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
        if attached_pages:
            logger.info("synth_attached_images", attached=len(attached_pages),
                        pages=[(p["doc_name"], p["page_num"]) for p in attached_pages])

    response = await client.aio.models.generate_content(
        model=settings.chat_synth_model,
        contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(
            system_instruction=_SYNTH_SYSTEM,
            temperature=0.2,
            max_output_tokens=4096,
            response_mime_type="application/json",
        ),
    )

    envelope = _parse_envelope(response.text or "")
    if not envelope:
        logger.warning("synth_envelope_parse_failed")
        return AgentResponse(
            status="no_evidence",
            answer=None,
            sources=sources,
            evidence=build_evidence(sources),
            assessment=None,
            attached_pages=attached_pages,
        )

    answer = envelope.get("answer")
    claims_raw = envelope.get("claims") or []
    unanswered_raw = envelope.get("unanswered") or []
    followups = envelope.get("followup_suggestions") or []

    claims = [
        Claim(
            text=str(c.get("text", "")).strip(),
            source_id=str(c.get("source_id", "")).strip(),
            confidence=str(c.get("confidence", "medium")).lower(),
        )
        for c in claims_raw
        if isinstance(c, dict) and c.get("text") and c.get("source_id")
    ]

    # Validate claims against sources (citation + numeric grounding).
    kept, dropped = validate_claims(claims, sources)
    if dropped:
        logger.info("synth_claims_dropped", count=len(dropped))

    unanswered = [
        Unanswered(
            facet=str(u.get("facet", "")).strip(),
            reason=str(u.get("reason", "")).strip(),
        )
        for u in unanswered_raw
        if isinstance(u, dict) and u.get("facet")
    ]

    followup = followups[0] if followups else None

    if answer is None or not str(answer).strip():
        status = "no_evidence"
    elif unanswered:
        status = "partial"
    else:
        status = "answered"

    return AgentResponse(
        status=status,
        answer=str(answer).strip() if answer else None,
        claims=kept,
        unanswered=unanswered,
        followup=followup,
        sources=sources,
        evidence=build_evidence(sources),
        attached_pages=attached_pages,
    )


# ---------------------------------------------------------------------------
# No-evidence soft landing
# ---------------------------------------------------------------------------

def _summarize_weak_hits(bundle: Bundle, max_items: int = 6) -> str:
    """Produce a short text summary of the weak hits to feed to Flash."""
    lines: list[str] = []
    for r in bundle.chunks[:3]:
        page = (r.page_num + 1) if isinstance(r.page_num, int) else "?"
        snippet = (r.text or "").strip().replace("\n", " ")
        if len(snippet) > 180:
            snippet = snippet[:180] + "…"
        lines.append(
            f"- chunk (score={r.score:.2f}) {r.doc_name or ''} p{page}: {snippet}"
        )
    for r in bundle.entities[:3]:
        page = (r.page_num + 1) if isinstance(r.page_num, int) else "?"
        lines.append(
            f"- entity (score={r.score:.2f}) {r.doc_name or ''} p{page}: "
            f"{r.entity_type or '?'} = {r.canonical_value or ''} {r.unit or ''}".rstrip()
        )
    for r in bundle.images[:2]:
        page = (r.page_num + 1) if isinstance(r.page_num, int) else "?"
        lines.append(
            f"- image (score={r.score:.2f}) {r.doc_name or ''} p{page}: "
            f"{r.image_type or 'figure'} — {(r.description or '')[:140]}"
        )
    return "\n".join(lines[:max_items]) or "(no related content found)"


async def synthesize_no_evidence(
    *,
    client: genai.Client,
    settings: Settings,
    user_message: str,
    plan: QueryPlan,
    bundle: Bundle,
    history_text: str = "",
) -> AgentResponse:
    """Generate a friendly soft-refusal that surfaces related content + followups.

    Used when the retrieval bundle is below the hard floor. Instead of a flat
    "I don't know", we tell the user:
      • what we couldn't answer,
      • what RELATED content is in the docs (from the weak hits),
      • 2-3 questions they might actually want to ask.
    """
    sources = build_sources(bundle)  # weak, but useful for the UI / debugging
    weak_summary = _summarize_weak_hits(bundle)

    history_block = (
        f"Conversation so far:\n{history_text}\n\n" if history_text else ""
    )
    user_block = (
        f"{history_block}"
        f"User question:\n{user_message}\n\n"
        f"Required facets the question implies: "
        f"{', '.join(plan.required_facets) if plan.required_facets else '(unspecified)'}\n\n"
        f"Weak/related hits from the ingested documents:\n{weak_summary}\n\n"
        "Write the soft-refusal JSON envelope now."
    )

    response = await client.aio.models.generate_content(
        model=settings.chat_synth_model,
        contents=[types.Content(
            role="user", parts=[types.Part.from_text(text=user_block)],
        )],
        config=types.GenerateContentConfig(
            system_instruction=_NO_EVIDENCE_SYSTEM,
            temperature=0.3,
            max_output_tokens=600,
            response_mime_type="application/json",
        ),
    )
    envelope = _parse_envelope(response.text or "")

    answer = (envelope.get("answer") or "").strip() if envelope else ""
    followups_raw = envelope.get("followup_suggestions") if envelope else None
    followups = [
        str(f).strip() for f in (followups_raw or []) if isinstance(f, str) and f.strip()
    ]

    if not answer:
        # Last-resort deterministic fallback so the user never gets nothing.
        answer = (
            "I couldn't find evidence in the ingested documents to confidently "
            "answer that question. "
        )
        if bundle.all_hits():
            answer += (
                "The closest related content I have is shown below — it may or "
                "may not be what you were looking for."
            )
        else:
            answer += (
                "Nothing in the currently ingested set looks related. You may "
                "want to upload a more relevant document or rephrase the question."
            )

    return AgentResponse(
        status="no_evidence",
        answer=answer,
        followup=followups[0] if followups else None,
        unanswered=[
            Unanswered(facet=f, reason="not present in retrieved sources")
            for f in plan.required_facets
        ],
        sources=sources,
        evidence=build_evidence(sources),
    )
