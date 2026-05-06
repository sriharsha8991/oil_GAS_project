"""Post-synthesis validation — citation grounding + numeric/unit echo check."""

from __future__ import annotations

import re
from typing import Any

from app.agents.rag.types import Claim
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Match a number with optional decimal, optional comma thousands, followed by a unit.
_NUMERIC_RE = re.compile(
    r"(?P<num>\d{1,3}(?:[,]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>ft|m|in|psi|bar|ppg|kg/m3|kg/m\^3|bbl|hrs?|°c|°f|c|f)\b",
    re.IGNORECASE,
)


def _norm_num(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _source_blob(sources: list[dict[str, Any]], source_id: str) -> str:
    for s in sources:
        if s.get("source_id") == source_id:
            bits = [
                s.get("text") or "",
                s.get("canonical_value") or "",
                s.get("table_markdown") or "",
                s.get("description") or "",
            ]
            return " ".join(b for b in bits if b).lower()
    return ""


def validate_claims(
    claims: list[Claim],
    sources: list[dict[str, Any]],
) -> tuple[list[Claim], list[Claim]]:
    """Return (kept, dropped) claims.

    Drops claims whose source_id doesn't exist in the source list, or whose
    numeric+unit values don't appear in the cited source's text within
    a reasonable tolerance.
    """
    valid_ids = {s.get("source_id") for s in sources}
    kept: list[Claim] = []
    dropped: list[Claim] = []

    for c in claims:
        if c.source_id not in valid_ids:
            logger.info("claim_dropped_bad_source", source_id=c.source_id)
            dropped.append(c)
            continue

        # Numeric grounding — if claim has number+unit, the cited source
        # must contain the same number+unit (within ±2% tolerance).
        nums_in_claim = list(_NUMERIC_RE.finditer(c.text))
        if nums_in_claim:
            blob = _source_blob(sources, c.source_id)
            ok = True
            for m in nums_in_claim:
                num = _norm_num(m.group("num"))
                unit = m.group("unit").lower()
                if num is None:
                    continue
                # Find any numeric in source with same unit, compare.
                source_matches = list(_NUMERIC_RE.finditer(blob))
                if not source_matches:
                    ok = False
                    break
                tolerance = max(abs(num) * 0.02, 0.5)
                hit = False
                for sm in source_matches:
                    s_num = _norm_num(sm.group("num"))
                    s_unit = sm.group("unit").lower()
                    if s_num is None:
                        continue
                    if s_unit != unit:
                        continue
                    if abs(s_num - num) <= tolerance:
                        hit = True
                        break
                if not hit:
                    ok = False
                    break
            if not ok:
                logger.info(
                    "claim_dropped_numeric_mismatch",
                    source_id=c.source_id,
                    claim=c.text[:120],
                )
                # Drop entirely — a wrong number with a citation is worse than
                # silence. The synthesizer can re-emit it as `unanswered`.
                dropped.append(c)
                continue

        kept.append(c)

    return kept, dropped
