"""Numeric guardrail (G-L1.6 / accuracy layer A5).

For every numeric entity returned by the extractor:

1. If the entity has a unit and the unit has a sane physical range
   (`app.domain.units.is_sane`), check that the value is plausible.
   Out-of-range values are flagged as `value_out_of_range`.

2. If the page has a non-empty PyMuPDF baseline text (i.e. the page is
   not purely scanned), check that the numeric token actually appears
   in the baseline. Missing tokens are flagged as `numeric_not_in_baseline`
   — they are the strongest signal of a hallucinated number.

The module is a pure function. The per-page graph wires it after the
extractor LLM call; any flag forces escalation to Pro by appending a gap
to the validation summary.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.units import is_sane

# Entity types whose `value` should be a single numeric quantity.
_NUMERIC_TYPES: set[str] = {
    "depth",
    "pressure",
    "temperature",
    "mud_weight",
    "fluid",        # mud weight / volume
    "casing",       # diameter
    "bit",          # diameter
}

# Maximum guardrail flags tolerated before forcing escalation.
_MAX_TOLERATED_FLAGS = 0

_NUMERIC_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def _parse_number(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    m = _NUMERIC_RE.match(s)
    if m is None:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _baseline_contains(value: float, baseline: str) -> bool:
    """Loose match — the number appears in the baseline as digits.

    Accepts the bare integer form, comma-grouped form, and one-decimal
    form. Avoids false positives by requiring a non-digit boundary on
    both sides (so `345` does not match `3450`).
    """
    if not baseline:
        return False

    # Build candidate string forms
    candidates: set[str] = set()
    if value.is_integer():
        ival = int(value)
        candidates.add(str(ival))
        candidates.add(f"{ival:,}")
    else:
        # Always include the raw float form
        s = ("%g" % value)
        candidates.add(s)
        # Also include 1- and 2-decimal forms to absorb formatting noise
        candidates.add(f"{value:.1f}")
        candidates.add(f"{value:.2f}")
        # Comma-grouped integer part
        int_part, _, frac = s.partition(".")
        try:
            candidates.add(f"{int(int_part):,}.{frac}" if frac else f"{int(int_part):,}")
        except ValueError:
            pass

    for cand in candidates:
        # Word-boundary-style check that ignores common punctuation
        pattern = r"(?<![\d.])" + re.escape(cand) + r"(?![\d])"
        if re.search(pattern, baseline):
            return True
    return False


def check_entities(
    entities: list[dict[str, Any]] | None,
    *,
    baseline_text: str = "",
    is_scanned: bool = False,
) -> list[dict[str, Any]]:
    """Run guardrails over a normalized entity list.

    Returns a list of flag dicts:
        {
          "kind": "value_out_of_range" | "numeric_not_in_baseline",
          "type": <entity type>,
          "value": <raw value>,
          "unit": <unit>,
        }
    """
    if not entities:
        return []

    flags: list[dict[str, Any]] = []
    can_check_baseline = bool(baseline_text) and not is_scanned

    for ent in entities:
        if not isinstance(ent, dict):
            continue
        etype = (ent.get("type") or "").strip().lower()
        if etype not in _NUMERIC_TYPES:
            continue
        unit = (ent.get("unit") or "").strip()
        raw_value = ent.get("value")
        num = _parse_number(raw_value)
        if num is None:
            # Non-numeric values for numeric-typed entities are themselves
            # suspicious but covered by the normalizer; skip here.
            continue

        if unit and not is_sane(num, unit):
            flags.append({
                "kind": "value_out_of_range",
                "type": etype,
                "value": raw_value,
                "unit": unit,
            })
            continue  # don't double-flag

        if can_check_baseline and not _baseline_contains(num, baseline_text):
            flags.append({
                "kind": "numeric_not_in_baseline",
                "type": etype,
                "value": raw_value,
                "unit": unit,
            })

    return flags


def should_force_escalation(flags: list[dict[str, Any]]) -> bool:
    """True when guardrail flag count justifies routing to Pro."""
    return len(flags) > _MAX_TOLERATED_FLAGS


def summarize_flags_for_gap(flags: list[dict[str, Any]]) -> str:
    """Compact human-readable summary appended to the extractor's gaps."""
    if not flags:
        return ""
    parts: list[str] = []
    for f in flags[:8]:  # cap noise
        parts.append(
            f"{f['kind']} ({f['type']}={f['value']!r} {f.get('unit', '')})".strip()
        )
    extra = "" if len(flags) <= 8 else f" (+{len(flags) - 8} more)"
    return "numeric guardrail: " + "; ".join(parts) + extra
