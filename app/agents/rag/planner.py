"""Planner — single Flash call that emits a structured QueryPlan.

Also exposes ``rewrite_for_gaps`` which regenerates queries focused on
the facets that the first retrieval pass failed to cover (recovery rung 2).
"""

from __future__ import annotations

import json
import re
from typing import Any

from google import genai
from google.genai import types

from app.agents.rag.types import ChatSession, EntityQuery, QueryPlan
from app.config import Settings
from app.utils.logging import get_logger

logger = get_logger(__name__)

_PLANNER_SYSTEM = """\
You are the query planner for a well-operations RAG system. The corpus contains
drilling reports, well summaries, BHA descriptions, NPT logs, casing programs,
mud reports, formation tops, and wellbore schematics.

Given the user's question (and short history), emit a JSON plan describing
how to search the document store. Output ONLY a JSON object — no prose.

Schema:
{
  "resolved_question": "the user's question rewritten as a single self-contained sentence with all coreferences resolved against the conversation history",
  "intent": "factual|tabular|visual|procedural|comparison|listing|unknown",
  "required_facets": ["short snake_case facets the answer must cover"],
  "chunk_queries": ["1-3 reformulated queries for narrative/table chunks"],
  "chunk_kinds": ["narrative"] | ["table"] | null,
  "entity_queries": [
    {"q":"short canonical phrase","entity_type":"depth|formation|npt_event|pressure|mud_weight|casing|bit|bha|well_id|temperature|null","unit":"ft|m|psi|ppg|null"}
  ],
  "image_queries": ["1-2 visual-content queries"],
  "image_types": ["schematic","log_strip","chart","cross_section","photo"] | null,
  "use_images": true | false,
  "needs_followup": false,
  "followup_question": null
}

Context resolution (CRITICAL for follow-ups):
- ALWAYS produce `resolved_question`. If the user's message has no
  coreferences it is identical to their message; otherwise rewrite it.
- Resolve every pronoun and elliptical reference against the history:
  "that one", "the deeper one", "it", "the next", "compared to before",
  "why", "more details", "show me", "what about X then?".
- Carry the SUBJECT from the previous assistant answer into the rewrite.
  Example:
    history: assistant said "The 9-5/8\" casing was set at 3,450 ft MD."
    user:    "and the 7\" one?"
    resolved_question: "What is the setting depth of the 7-inch casing?"
- If the user is asking for elaboration ("more details", "why", "explain"),
  keep the previous subject and add the new angle:
    "more details about the kick on May 12" →
    "What were the operational details, mud weight changes, and shut-in
     pressures during the kick on May 12?"
- Build `chunk_queries`, `entity_queries`, `image_queries`, and
  `required_facets` from the RESOLVED question, not the literal user message.

Rules:
- Use HyDE-style rewrites for chunk_queries: write what the answer *would say*,
  in the document's language (e.g. "shoe depth", "casing point", "kick-off depth").
- entity_queries must be SHORT canonical strings ("9-5/8 in casing", not "what is the casing").
- Set use_images=true only if the question explicitly or implicitly needs a figure
  (schematic, log, chart, cross-section, "show me", "diagram of...").
- Set needs_followup=true ONLY when the question is genuinely ambiguous AFTER
  context resolution (multiple wells could match, vague "the casing" with no
  doc context AND no prior assistant turn to anchor on, etc).
- chunk_kinds=["table"] when the user is asking for a structured listing
  ("casing program", "BHA list", "depth table"). null = both.
- required_facets are the atomic things the answer needs. Examples:
    "casing setting depth question" -> ["casing_size","setting_depth"]
    "all NPT events"                -> ["npt_event_list"]
    "BHA on run 3"                  -> ["bha_components","run_number"]
"""

_REWRITE_SYSTEM = """\
You are recovering a RAG retrieval that came back weak. Given the original
question, the original plan, and the facets that are still MISSING after the
first retrieval pass, output a NEW plan with rewritten queries that target
those missing facets specifically. Same JSON schema as before. Output JSON only.
"""


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_plan_json(text: str) -> dict[str, Any]:
    if not text:
        return {}
    # Strip ```json fences if present
    cleaned = text.strip()
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


def _to_plan(raw: dict[str, Any]) -> QueryPlan:
    eqs_raw = raw.get("entity_queries") or []
    entity_queries: list[EntityQuery] = []
    for e in eqs_raw:
        if not isinstance(e, dict):
            continue
        q = (e.get("q") or "").strip()
        if not q:
            continue
        et = e.get("entity_type")
        unit = e.get("unit")
        entity_queries.append(EntityQuery(
            q=q,
            entity_type=et if isinstance(et, str) and et else None,
            unit=unit if isinstance(unit, str) and unit else None,
        ))

    chunk_kinds = raw.get("chunk_kinds")
    if isinstance(chunk_kinds, list):
        chunk_kinds = [k for k in chunk_kinds if k in ("narrative", "table")] or None
    else:
        chunk_kinds = None

    image_types = raw.get("image_types")
    if isinstance(image_types, list):
        image_types = [t for t in image_types if isinstance(t, str)] or None
    else:
        image_types = None

    return QueryPlan(
        intent=str(raw.get("intent") or "factual"),
        resolved_question=(
            str(raw["resolved_question"]).strip()
            if isinstance(raw.get("resolved_question"), str)
            and raw["resolved_question"].strip()
            else None
        ),
        required_facets=[
            str(f) for f in (raw.get("required_facets") or []) if isinstance(f, str)
        ],
        chunk_queries=[
            str(q) for q in (raw.get("chunk_queries") or []) if isinstance(q, str) and q.strip()
        ],
        chunk_kinds=chunk_kinds,
        entity_queries=entity_queries,
        image_queries=[
            str(q) for q in (raw.get("image_queries") or []) if isinstance(q, str) and q.strip()
        ],
        image_types=image_types,
        use_images=bool(raw.get("use_images", False)),
        needs_followup=bool(raw.get("needs_followup", False)),
        followup_question=raw.get("followup_question") or None,
        raw=raw,
    )


def _fallback_plan(user_message: str) -> QueryPlan:
    """If the planner emits unparseable JSON, do a sane default fan-out."""
    return QueryPlan(
        intent="unknown",
        resolved_question=user_message,
        required_facets=[],
        chunk_queries=[user_message],
        chunk_kinds=None,
        entity_queries=[EntityQuery(q=user_message)],
        image_queries=[user_message],
        image_types=None,
        use_images=True,
        needs_followup=False,
        followup_question=None,
        raw={"fallback": True},
    )


def _history_text(session: ChatSession, limit: int = 4) -> str:
    if not session.messages:
        return ""
    tail = session.messages[-limit:]
    return "\n".join(f"{m.role}: {m.content}" for m in tail)


async def plan(
    *,
    client: genai.Client,
    settings: Settings,
    user_message: str,
    session: ChatSession,
) -> QueryPlan:
    """Run the planner Flash call and return a QueryPlan."""
    history = _history_text(session)
    user_prompt = (
        f"History:\n{history or '(none)'}\n\n"
        f"User question: {user_message}\n\n"
        "Emit the plan JSON now."
    )

    response = await client.aio.models.generate_content(
        model=settings.chat_planner_model,
        contents=[types.Content(
            role="user", parts=[types.Part.from_text(text=user_prompt)],
        )],
        config=types.GenerateContentConfig(
            system_instruction=_PLANNER_SYSTEM,
            temperature=0.1,
            max_output_tokens=1024,
            response_mime_type="application/json",
        ),
    )
    raw = _parse_plan_json(response.text or "")
    if not raw:
        logger.warning("planner_fallback", message=user_message[:120])
        return _fallback_plan(user_message)

    qp = _to_plan(raw)
    logger.info(
        "planner_done",
        intent=qp.intent,
        facets=qp.required_facets,
        chunk_qs=len(qp.chunk_queries),
        entity_qs=len(qp.entity_queries),
        use_images=qp.use_images,
        followup=qp.needs_followup,
    )
    return qp


async def rewrite_for_gaps(
    *,
    client: genai.Client,
    settings: Settings,
    user_message: str,
    original: QueryPlan,
    missing_facets: list[str],
) -> QueryPlan:
    """Recovery rung 2 — rewrite queries to target missing facets."""
    payload = {
        "original_question": user_message,
        "original_plan": original.raw or {
            "intent": original.intent,
            "required_facets": original.required_facets,
            "chunk_queries": original.chunk_queries,
            "entity_queries": [
                {"q": e.q, "entity_type": e.entity_type, "unit": e.unit}
                for e in original.entity_queries
            ],
            "image_queries": original.image_queries,
        },
        "missing_facets": missing_facets,
    }

    response = await client.aio.models.generate_content(
        model=settings.chat_planner_model,
        contents=[types.Content(
            role="user", parts=[types.Part.from_text(text=json.dumps(payload))],
        )],
        config=types.GenerateContentConfig(
            system_instruction=_REWRITE_SYSTEM,
            temperature=0.2,
            max_output_tokens=1024,
            response_mime_type="application/json",
        ),
    )
    raw = _parse_plan_json(response.text or "")
    if not raw:
        logger.warning("rewrite_fallback", missing=missing_facets)
        # Fall back to broadening: same queries, no kind filter, no entity_type filter.
        broadened = QueryPlan(
            intent=original.intent,
            required_facets=missing_facets,
            chunk_queries=[*original.chunk_queries, user_message],
            chunk_kinds=None,
            entity_queries=[EntityQuery(q=e.q) for e in original.entity_queries] or [
                EntityQuery(q=user_message)
            ],
            image_queries=original.image_queries or [user_message],
            image_types=None,
            use_images=original.use_images,
            raw={"fallback_rewrite": True},
        )
        return broadened

    qp = _to_plan(raw)
    logger.info(
        "planner_rewrite_done",
        missing=missing_facets,
        chunk_qs=len(qp.chunk_queries),
        entity_qs=len(qp.entity_queries),
    )
    return qp
