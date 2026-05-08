"""Extractor agent — Gemini 3 Flash Preview self-validating per-page extraction.

The extractor is a single-shot node that returns BOTH the extracted content
and its own confidence + gap assessment. The graph routes directly to the
escalator (Pro) when self_confidence is below threshold or gaps is non-empty.
"""

from __future__ import annotations

import json
import time

from app.agents.prompts import EXTRACTOR_SYSTEM, build_extractor_prompt
from app.models.state import PageState
from app.pipeline.entity_normalizer import (
    normalize_entities,
    should_force_escalation as normalizer_force_escalate,
)
from app.pipeline.numeric_guardrail import (
    check_entities as guardrail_check,
    should_force_escalation as guardrail_force_escalate,
    summarize_flags_for_gap,
)
from app.utils.gemini_client import GeminiClient
from app.utils.logging import get_logger

logger = get_logger(__name__)


async def extractor_node(
    state: PageState,
    *,
    client: GeminiClient,
    model: str,
    pass_threshold: float,
) -> PageState:
    """LangGraph node: extract content from one PDF page (PNG bytes) AND self-validate."""
    page_num = state["page_num"]

    prompt = build_extractor_prompt(
        page_num=page_num,
        is_scanned=state.get("is_scanned", False),
        baseline_text=state.get("baseline_text", ""),
        pass_threshold=pass_threshold,
    )

    start = time.perf_counter()
    raw, usage = await client.generate_with_image(
        model=model,
        prompt=prompt,
        png_bytes=state["png_bytes"],
        system_instruction=EXTRACTOR_SYSTEM,
        response_mime_type="application/json",
        temperature=0.1,
    )
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    extracted, validation = _parse_extraction(raw, page_num, pass_threshold)

    # G-L1.3: coerce entities to controlled vocabulary.
    normalized_entities, normalizer_warnings = normalize_entities(
        extracted.get("entities", [])
    )
    extracted["entities"] = normalized_entities

    # G-L1.6: numeric guardrail (range + baseline cross-check).
    guardrail_flags = guardrail_check(
        normalized_entities,
        baseline_text=state.get("baseline_text", ""),
        is_scanned=state.get("is_scanned", False),
    )

    # Fold both into the validation summary so escalation receives them as gaps.
    extra_gaps: list[str] = []
    if normalizer_warnings:
        extra_gaps.append(
            "entity vocabulary: " + "; ".join(normalizer_warnings[:8])
            + ("" if len(normalizer_warnings) <= 8
               else f" (+{len(normalizer_warnings) - 8} more)")
        )
    guardrail_gap = summarize_flags_for_gap(guardrail_flags)
    if guardrail_gap:
        extra_gaps.append(guardrail_gap)

    if extra_gaps:
        validation["gaps"] = list(validation.get("gaps", [])) + extra_gaps
    if (
        normalizer_force_escalate(normalizer_warnings)
        or guardrail_force_escalate(guardrail_flags)
    ):
        validation["passed"] = False

    logger.info(
        "extractor_done",
        page_num=page_num,
        time_ms=elapsed_ms,
        chars=len(extracted.get("markdown", "")),
        self_confidence=validation["confidence"],
        self_gaps=len(validation["gaps"]),
        passed=validation["passed"],
        normalizer_warnings=len(normalizer_warnings),
        guardrail_flags=len(guardrail_flags),
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
    )

    trace = list(state.get("trace", []))
    trace.append({
        "phase": "extractor",
        "model": model,
        "iteration": 1,
        "time_ms": elapsed_ms,
        "output_chars": len(raw),
        "self_confidence": validation["confidence"],
        "passed": validation["passed"],
        "normalizer_warnings": len(normalizer_warnings),
        "guardrail_flags": len(guardrail_flags),
        "usage": usage,
    })

    return {
        **state,
        "extracted": extracted,
        "validation": validation,
        "guardrail_flags": guardrail_flags,
        "iteration": 1,
        "trace": trace,
    }


def _parse_extraction(
    raw: str, page_num: int, pass_threshold: float
) -> tuple[dict, dict]:
    """Parse the JSON response. Returns (extracted_dict, validation_dict)."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        nl = cleaned.index("\n") if "\n" in cleaned else len(cleaned)
        cleaned = cleaned[nl + 1:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.error("extractor_parse_error", page_num=page_num, raw_preview=raw[:300])
        # Force escalation on parse failure.
        return (
            {
                "page_num": page_num,
                "markdown": "",
                "tables": [],
                "images": [],
                "entities": [],
                "cross_page_notes": "",
            },
            {
                "passed": False,
                "confidence": 0.0,
                "gaps": ["extractor response was not valid JSON; re-extract the page"],
            },
        )

    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        data = {}

    extracted = {
        "page_num": data.get("page_num", page_num),
        "markdown": data.get("markdown", ""),
        "tables": data.get("tables", []),
        "images": data.get("images", []),
        "entities": data.get("entities", []),
        "cross_page_notes": data.get("cross_page_notes", ""),
    }

    try:
        confidence = float(data.get("self_confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    gaps_raw = data.get("self_gaps") or []
    if not isinstance(gaps_raw, list):
        gaps_raw = [str(gaps_raw)]
    gaps = [str(g).strip() for g in gaps_raw if str(g).strip()]

    passed = confidence >= pass_threshold and not gaps

    validation = {
        "passed": passed,
        "confidence": confidence,
        "gaps": gaps,
    }

    return extracted, validation
