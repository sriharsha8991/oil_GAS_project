"""Escalation agent — Gemini 3.1 Pro Preview FINAL extraction.

Triggered only when the extractor's self-validation fails (confidence below
threshold or non-empty gaps). Receives the previous extraction JSON and the
list of flagged gaps so the Pro model knows exactly where to focus.
"""

from __future__ import annotations

import json
import time

from app.agents.prompts import ESCALATION_SYSTEM, build_escalation_prompt
from app.models.state import PageState
from app.utils.gemini_client import GeminiClient
from app.utils.logging import get_logger

logger = get_logger(__name__)


async def escalation_node(
    state: PageState,
    *,
    client: GeminiClient,
    model: str,
) -> PageState:
    """LangGraph node: re-extract one page with Gemini 3.1 Pro Preview. Output is FINAL."""
    page_num = state["page_num"]

    validation = state.get("validation", {})
    previous_confidence = float(validation.get("confidence", 0.0) or 0.0)
    previous_gaps = "\n".join(f"- {g}" for g in validation.get("gaps", []))

    previous_extraction_json = ""
    extracted = state.get("extracted") or {}
    if extracted:
        try:
            previous_extraction_json = json.dumps(extracted, ensure_ascii=False)
        except (TypeError, ValueError):
            previous_extraction_json = ""

    prompt = build_escalation_prompt(
        page_num=page_num,
        is_scanned=state.get("is_scanned", False),
        baseline_text=state.get("baseline_text", ""),
        previous_extraction_json=previous_extraction_json,
        previous_confidence=previous_confidence,
        previous_gaps=previous_gaps,
    )

    start = time.perf_counter()
    raw, usage = await client.generate_with_image(
        model=model,
        prompt=prompt,
        png_bytes=state["png_bytes"],
        system_instruction=ESCALATION_SYSTEM,
        response_mime_type="application/json",
        temperature=0.1,
    )
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    final = _parse_extraction(raw, page_num)

    logger.info(
        "escalation_done",
        page_num=page_num,
        time_ms=elapsed_ms,
        chars=len(final.get("markdown", "")),
        previous_confidence=previous_confidence,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
    )

    trace = list(state.get("trace", []))
    trace.append({
        "phase": "escalation",
        "model": model,
        "iteration": 2,
        "time_ms": elapsed_ms,
        "output_chars": len(raw),
        "previous_confidence": previous_confidence,
        "usage": usage,
    })

    return {
        **state,
        "extracted": final,
        "final": final,
        "iteration": 2,
        "escalated": True,
        "trace": trace,
    }


def _parse_extraction(raw: str, page_num: int) -> dict:
    """Parse the JSON object response (same shape as extractor, ignoring self-fields)."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        nl = cleaned.index("\n") if "\n" in cleaned else len(cleaned)
        cleaned = cleaned[nl + 1:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.error("escalation_parse_error", page_num=page_num, raw_preview=raw[:300])
        return {
            "page_num": page_num,
            "markdown": "",
            "tables": [],
            "images": [],
            "entities": [],
            "cross_page_notes": "",
        }

    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        data = {}

    return {
        "page_num": data.get("page_num", page_num),
        "markdown": data.get("markdown", ""),
        "tables": data.get("tables", []),
        "images": data.get("images", []),
        "entities": data.get("entities", []),
        "cross_page_notes": data.get("cross_page_notes", ""),
    }
