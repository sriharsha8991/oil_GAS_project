"""Extractor agent — Gemini 3 Flash Preview per-page extraction with inline image bytes."""

from __future__ import annotations

import json
import time

from app.agents.prompts import EXTRACTOR_SYSTEM, build_extractor_prompt
from app.models.state import PageState
from app.utils.gemini_client import GeminiClient
from app.utils.logging import get_logger

logger = get_logger(__name__)


async def extractor_node(
    state: PageState,
    *,
    client: GeminiClient,
    model: str,
) -> PageState:
    """LangGraph node: extract content from one PDF page (PNG bytes).

    On iter 1, baseline is passed as a hint. On iter 2, previous gaps
    from the validator are appended as retry instructions.
    """
    page_num = state["page_num"]
    iteration = state.get("iteration", 0) + 1

    gap_feedback = ""
    if iteration > 1 and state.get("validation"):
        gaps = state["validation"].get("gaps", [])
        if gaps:
            gap_feedback = "\n".join(f"- {g}" for g in gaps)

    prompt = build_extractor_prompt(
        page_num=page_num,
        is_scanned=state.get("is_scanned", False),
        baseline_text=state.get("baseline_text", ""),
        gap_feedback=gap_feedback,
    )

    start = time.perf_counter()
    raw = await client.generate_with_image(
        model=model,
        prompt=prompt,
        png_bytes=state["png_bytes"],
        system_instruction=EXTRACTOR_SYSTEM,
        response_mime_type="application/json",
        temperature=0.1,
    )
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    extracted = _parse_extraction(raw, page_num)

    logger.info(
        "extractor_done",
        page_num=page_num,
        iteration=iteration,
        time_ms=elapsed_ms,
        chars=len(extracted.get("markdown", "")),
    )

    trace = list(state.get("trace", []))
    trace.append({
        "phase": "extractor",
        "model": model,
        "iteration": iteration,
        "time_ms": elapsed_ms,
        "output_chars": len(raw),
    })

    return {
        **state,
        "extracted": extracted,
        "iteration": iteration,
        "trace": trace,
    }


def _parse_extraction(raw: str, page_num: int) -> dict:
    """Parse the JSON object response from the extractor."""
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
        return {
            "page_num": page_num,
            "markdown": "",
            "tables": [],
            "images": [],
            "entities": [],
            "cross_page_notes": "",
        }

    # If the model returned a list (one-element wrapper), unwrap
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
