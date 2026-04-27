"""Escalation agent — Gemini 3.1 Pro Preview final extraction (iter 3, no further validation)."""

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
    """LangGraph node: re-extract one page with Gemini 3.1 Pro Preview. Output is FINAL.

    Receives all gap descriptions from prior validator runs.
    """
    page_num = state["page_num"]

    # Collect gap history from the trace + the latest validation
    previous_gaps = _collect_gaps(state)

    prompt = build_escalation_prompt(
        page_num=page_num,
        is_scanned=state.get("is_scanned", False),
        baseline_text=state.get("baseline_text", ""),
        previous_gaps=previous_gaps,
    )

    start = time.perf_counter()
    raw = await client.generate_with_image(
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
    )

    trace = list(state.get("trace", []))
    trace.append({
        "phase": "escalation",
        "model": model,
        "iteration": 3,
        "time_ms": elapsed_ms,
        "output_chars": len(raw),
    })

    return {
        **state,
        "extracted": final,
        "final": final,
        "iteration": 3,
        "escalated": True,
        "trace": trace,
    }


def _collect_gaps(state: PageState) -> str:
    """Build a human-readable gap summary from the validation result."""
    validation = state.get("validation", {})
    gaps = validation.get("gaps", [])
    if not gaps:
        return "No specific gaps recorded; previous attempts had low overall confidence."
    return "\n".join(f"- {g}" for g in gaps)


def _parse_extraction(raw: str, page_num: int) -> dict:
    """Parse the JSON object response (same shape as extractor)."""
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
