"""Source verifier — post-answer pass that re-reads cited chunks and
downgrades / strips unsupported claims.

Lightweight contract:

* Parse ``[Sn]`` citation markers from the answer.
* Drop citations that point to a source id we never returned (the model
  hallucinated it).
* Run one Flash call asking whether each citation is actually supported
  by its source text. Strip unsupported citations from the answer.

Returns the (possibly rewritten) answer plus a verification report
that bubbles into ``meta.verification`` so the FE can render a badge
when something was downgraded.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from google import genai
from google.genai import types

from app.config import Settings
from app.utils.logging import get_logger

logger = get_logger(__name__)

_CITE_RE = re.compile(r"\[(S\d+)\]")
_VERIFIER_SYSTEM = (
    "You are an answer-grounding verifier. For each (claim sentence, cited "
    "source snippet) pair you are given, decide if the snippet directly "
    "supports the claim. Reply with a single JSON array — one object per "
    "pair — using exactly: "
    '{"source_id": "Sn", "supported": true|false, "reason": "<short>"}. '
    "Do not add prose, do not wrap in code fences."
)


@dataclass
class VerificationReport:
    cited_ids: list[str] = field(default_factory=list)
    unknown_ids: list[str] = field(default_factory=list)        # cited but not in sources
    unsupported_ids: list[str] = field(default_factory=list)    # cited but not grounded
    checked: int = 0
    skipped: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cited_ids": self.cited_ids,
            "unknown_ids": self.unknown_ids,
            "unsupported_ids": self.unsupported_ids,
            "checked": self.checked,
            "skipped": self.skipped,
            "error": self.error,
        }


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p.strip()]


def _claim_for_citation(answer: str, cite: str) -> str:
    """Return the sentence that contains ``cite`` (e.g. ``[S2]``)."""
    for s in _split_sentences(answer):
        if cite in s:
            return s.strip()
    return cite


def _strip_citation(answer: str, source_id: str) -> str:
    """Remove every ``[Sn]`` reference for ``source_id`` from ``answer``."""
    pat = re.compile(r"\s*\[" + re.escape(source_id) + r"\]")
    return pat.sub("", answer)


async def verify_answer(
    *,
    client: genai.Client,
    settings: Settings,
    answer: str,
    sources: list[dict[str, Any]],
) -> tuple[str, VerificationReport]:
    """Re-read cited chunks; strip unsupported / unknown citations.

    Returns ``(possibly_rewritten_answer, report)``.
    """
    report = VerificationReport()
    if not answer or not sources:
        report.skipped = True
        return answer, report

    cited = list(dict.fromkeys(_CITE_RE.findall(answer)))
    if not cited:
        report.skipped = True
        return answer, report

    by_id: dict[str, dict[str, Any]] = {
        s["source_id"]: s for s in sources if s.get("source_id")
    }
    report.cited_ids = cited

    # 1. Unknown citations — model hallucinated an Sn id we never returned.
    rewritten = answer
    for cite in cited:
        if cite not in by_id:
            report.unknown_ids.append(cite)
            rewritten = _strip_citation(rewritten, cite)

    real_cited = [c for c in cited if c in by_id]
    if not real_cited:
        return rewritten, report

    # 2. Semantic check — one Flash call, JSON list out.
    pairs: list[dict[str, str]] = []
    for cite in real_cited:
        src = by_id[cite]
        snippet = (
            src.get("table_markdown")
            or src.get("text")
            or src.get("description")
            or src.get("canonical_value")
            or ""
        )
        pairs.append({
            "source_id": cite,
            "claim": _claim_for_citation(rewritten, cite),
            "snippet": snippet[:1500],
        })

    user_text = (
        "Verify the following claim/snippet pairs:\n\n"
        + json.dumps(pairs, ensure_ascii=False)
    )
    try:
        resp = await client.aio.models.generate_content(
            model=settings.chat_synth_model,
            contents=[types.Content(
                role="user", parts=[types.Part.from_text(text=user_text)],
            )],
            config=types.GenerateContentConfig(
                system_instruction=_VERIFIER_SYSTEM,
                temperature=0.0,
                max_output_tokens=512,
                response_mime_type="application/json",
            ),
        )
        raw = (getattr(resp, "text", "") or "").strip()
        verdicts = json.loads(raw)
        if not isinstance(verdicts, list):
            raise ValueError("verifier did not return a list")
    except Exception as exc:  # noqa: BLE001
        logger.warning("source_verifier_failed", error=str(exc))
        report.error = type(exc).__name__
        return rewritten, report

    report.checked = len(verdicts)
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        sid = v.get("source_id")
        if sid in by_id and v.get("supported") is False:
            report.unsupported_ids.append(sid)
            rewritten = _strip_citation(rewritten, sid)

    return rewritten, report
