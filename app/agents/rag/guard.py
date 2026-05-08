"""Guard LLM — single Flash call that routes every chat turn.

Three possible decisions:
  1. ``reply``       — answer directly from the context card / chitchat /
                       app help. No RAG, no synth call.
  2. ``synthesize``  — domain question; delegate to the synthesizer with a
                       coreference-resolved version of the user's query.
  3. ``clarify``     — ambiguous question we cannot disambiguate from the
                       context card or history; emit ONE clarifying
                       question and stop.

A free regex pre-filter catches trivial greetings before any LLM call.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from google import genai
from google.genai import types

from app.config import Settings
from app.utils.logging import get_logger

logger = get_logger(__name__)


GuardAction = Literal["reply", "synthesize", "clarify"]


_GREETING_RE = re.compile(
    r"^("
    r"hi|hii+|hello+|hey+|yo|sup|howdy|"
    r"good\s+(morning|afternoon|evening|day)|"
    r"thanks?|thank\s+you|thx|ty|cheers|"
    r"ok|okay|cool|nice|great|awesome|got\s+it"
    r")[!.?\s]*$",
    re.IGNORECASE,
)

_GREETING_REPLY = (
    "Hi! I'm WellSynth. I can answer questions about your uploaded "
    "well-operations documents, or about your projects, ingestion status, "
    "and account. What would you like to know?"
)


@dataclass
class GuardDecision:
    action: GuardAction
    reply: str = ""
    query: str = ""
    clarification: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Layer 0 — free pre-filter
# ---------------------------------------------------------------------------

def prefilter(message: str) -> GuardDecision | None:
    """Return a canned reply for trivial greetings, or None to escalate.

    Refuses to match anything containing a question mark, a digit, or
    more than 5 whitespace-separated tokens — those are always escalated.
    """
    if not message or not message.strip():
        return None
    msg = message.strip()
    if "?" in msg or any(ch.isdigit() for ch in msg):
        return None
    if len(msg.split()) > 5:
        return None
    if _GREETING_RE.match(msg):
        return GuardDecision(action="reply", reply=_GREETING_REPLY)
    return None


# ---------------------------------------------------------------------------
# Layer 1 — guard LLM
# ---------------------------------------------------------------------------

_GUARD_SYSTEM = """\
You are WellSynth's chat guard — the single decision-maker for every user turn.

You receive:
- The user's latest message.
- Recent conversation history (for resolving coreferences like "that one",
  "the deeper one", "compared to before").
- A CONTEXT CARD describing the user's profile, current scope, projects,
  recent documents, in-progress jobs, and permissions.

You MUST output exactly one JSON object — no prose, no markdown fences:

{
  "action": "reply" | "synthesize" | "clarify",
  "reply": "<final user-facing text when action=reply, else empty>",
  "query": "<resolved domain question for the synthesizer when action=synthesize, else empty>",
  "clarification": "<single short question when action=clarify, else empty>"
}

Choose `action` per these rules:

- "reply": You can fully answer using ONLY the CONTEXT CARD or general
  assistant chitchat. Use this for:
    * greetings, thanks, acknowledgements, off-topic chatter
    * questions about the user's role, organization, quota, identity
    * questions about which projects/documents they have, ingestion progress,
      page counts, what they are currently scoped to
    * "what can you do", "how does this work", capability questions
  Reply in plain text under 80 words. Never claim to have read a document
  or quote its contents. Never name a document not in the CONTEXT CARD.

- "synthesize": ANY question that requires reading the CONTENTS of a
  document (facts, dates, depths, formations, equipment, narrative,
  "what does it say about X", "summarize", "page N", "tell me about ...").
  Set `query` to the user's question with all coreferences resolved
  against history (e.g. "the deeper one" → the actual well/depth from
  the prior turn). Leave `reply` empty.

- "clarify": ONLY when the question is genuinely ambiguous AND the
  conversation history does NOT disambiguate it (e.g. user has multiple
  documents in scope and asks "what was the casing?" with no prior
  subject). Use sparingly. Set `clarification` to one short question.

Hard rules:
1. Never invent facts about document contents in `reply`. If unsure → "synthesize".
2. Never name a document, project, or well that's not in the CONTEXT CARD.
3. When in doubt between any meta bucket and "synthesize" → choose "synthesize".
   A short partial answer from real evidence is always better than a
   confident-sounding meta reply.
4. The user's domain question is what you must preserve in `query`. Do not
   summarise it; do not compress it. Just resolve the coreferences.
"""


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _safe_parse_json(text: str) -> dict[str, Any]:
    if not text:
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.MULTILINE
        )
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


async def guard(
    *,
    message: str,
    history_text: str,
    card: dict[str, Any],
    settings: Settings,
    client: genai.Client | None = None,
) -> GuardDecision:
    """Run the guard LLM and return a structured decision.

    On any error the default is ``synthesize`` with the raw user message —
    we'd rather over-search than strand the user with a vague meta reply.
    """
    cli = client or genai.Client(api_key=settings.gemini_api_key)
    model = (
        getattr(settings, "chat_triage_model", None)
        or settings.chat_planner_model
    )

    user_block = (
        "CONTEXT CARD:\n"
        + json.dumps(card, ensure_ascii=False, default=str)
        + "\n\nConversation history:\n"
        + (history_text or "(none)")
        + "\n\nUser message:\n"
        + message.strip()
    )

    try:
        resp = await cli.aio.models.generate_content(
            model=model,
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=user_block)],
                )
            ],
            config=types.GenerateContentConfig(
                system_instruction=_GUARD_SYSTEM,
                temperature=0.1,
                max_output_tokens=1024,
                response_mime_type="application/json",
            ),
        )
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning("guard_llm_error", error=str(exc),
                       error_type=type(exc).__name__)
        return GuardDecision(
            action="synthesize", query=message.strip(),
            raw={"error": str(exc)},
        )

    text = (getattr(resp, "text", "") or "").strip()
    raw = _safe_parse_json(text)
    in_tok = 0
    out_tok = 0
    usage = getattr(resp, "usage_metadata", None)
    if usage is not None:
        in_tok = int(getattr(usage, "prompt_token_count", 0) or 0)
        out_tok = int(getattr(usage, "candidates_token_count", 0) or 0)

    if not raw:
        logger.warning("guard_parse_failed", text_head=text[:200])
        return GuardDecision(
            action="synthesize", query=message.strip(),
            input_tokens=in_tok, output_tokens=out_tok,
            raw={"text_head": text[:200]},
        )

    action = str(raw.get("action") or "").strip().lower()
    reply = str(raw.get("reply") or "").strip()
    query = str(raw.get("query") or "").strip()
    clarification = str(raw.get("clarification") or "").strip()

    # Validate + repair.
    if action not in ("reply", "synthesize", "clarify"):
        action = "synthesize"
    if action == "synthesize" and not query:
        query = message.strip()
    if action == "clarify" and not clarification:
        # No clarification text → fall through to synth.
        action = "synthesize"
        query = query or message.strip()
    if action == "reply" and not reply:
        # Empty reply → fall through to synth so user gets something real.
        action = "synthesize"
        query = message.strip()

    return GuardDecision(
        action=action,  # type: ignore[arg-type]
        reply=reply, query=query, clarification=clarification,
        input_tokens=in_tok, output_tokens=out_tok, raw=raw,
    )
