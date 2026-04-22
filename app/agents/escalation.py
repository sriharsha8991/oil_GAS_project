"""Escalation agent node — re-extracts using Gemini Pro 2.5.

Called when the validator fails a batch after max_retries iterations.
Pro 2.5 has stronger vision and reasoning for edge cases like
handwritten annotations, extremely dense tables, and degraded scans.

Escalation output is FINAL — no further validation loop to avoid
cost explosion.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.agents.prompts import ESCALATION_SYSTEM, build_escalation_prompt
from app.models.state import BatchState
from app.utils.gemini_client import GeminiClient
from app.utils.logging import get_logger

logger = get_logger(__name__)


async def escalation_node(
    state: BatchState,
    client: GeminiClient,
    model: str,
    uploaded_file: Any | None = None,
) -> BatchState:
    """LangGraph node: re-extract with Pro 2.5 after Flash failures.

    Note: No cached_content — Pro may use a different model than the cache.
    The uploaded file is sent directly.

    Args:
        state: Current graph state with validation failures.
        client: GeminiClient instance.
        model: Escalation model name (e.g. "models/gemini-2.5-pro").
        uploaded_file: Uploaded PDF file reference.

    Returns:
        Updated state with final_pages from Pro extraction.
    """
    page_start = state["page_start"]
    page_end = state["page_end"]
    section_context = state.get("section_context", "")

    # Collect all gap feedback from validation
    previous_gaps = _collect_gaps(state)

    prompt = build_escalation_prompt(
        page_start=page_start,
        page_end=page_end,
        section_context=section_context,
        previous_gaps=previous_gaps,
    )

    contents: list[Any] = []
    if uploaded_file:
        contents.append(uploaded_file)
    contents.append(prompt)

    start = time.perf_counter()
    raw_response = await client.generate(
        model=model,
        contents=contents,
        system_instruction=ESCALATION_SYSTEM,
        response_mime_type="application/json",
        temperature=0.1,
    )
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    # Parse response
    extracted = _parse_escalation_response(raw_response, page_start, page_end)

    logger.info(
        "escalation_done",
        batch_id=state.get("batch_id"),
        pages=f"{page_start}-{page_end}",
        extracted_pages=len(extracted),
        time_ms=elapsed_ms,
    )

    trace_entry = {
        "phase": "escalation",
        "model": model,
        "cached": False,
        "output_chars": len(raw_response),
        "time_ms": elapsed_ms,
    }

    trace_log = list(state.get("trace_log", []))
    trace_log.append(trace_entry)

    return {
        **state,
        "final_pages": extracted,
        "escalated": True,
        "trace_log": trace_log,
    }


def _collect_gaps(state: BatchState) -> str:
    """Collect all gap descriptions from the validation result."""
    vr = state.get("validation_result", {})
    if not vr:
        return "No specific gaps recorded."

    lines: list[str] = []
    for pr in vr.get("page_results", []):
        gaps = pr.get("gaps", [])
        if gaps:
            lines.append(f"Page {pr.get('page_num', '?')}:")
            for g in gaps:
                lines.append(f"  - {g}")

    return "\n".join(lines) if lines else "No specific gaps recorded."


def _parse_escalation_response(
    raw: str,
    page_start: int,
    page_end: int,
) -> list[dict]:
    """Parse the JSON array response from the escalation model."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.index("\n") if "\n" in cleaned else len(cleaned)
        cleaned = cleaned[first_nl + 1:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.error("escalation_parse_error", raw_preview=raw[:300])
        return [
            {"page_num": p, "markdown": "", "tables": [], "images": [], "cross_page_notes": ""}
            for p in range(page_start, page_end + 1)
        ]

    if not isinstance(data, list):
        data = [data]

    pages: list[dict] = []
    for item in data:
        page = {
            "page_num": item.get("page_num", page_start),
            "markdown": item.get("markdown", ""),
            "tables": item.get("tables", []),
            "images": item.get("images", []),
            "cross_page_notes": item.get("cross_page_notes", ""),
        }
        pages.append(page)

    return pages
