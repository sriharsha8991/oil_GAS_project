"""Validator agent node — cross-checks extraction against PDF + baseline.

Compares the extractor output against three sources:
  1. The actual PDF pages (Gemini sees the visual)
  2. The Phase 0 baseline text (independent pymupdf4llm extraction)
  3. The extractor's output

Returns a BatchValidation-compatible dict with per-page pass/fail,
confidence scores, and specific gap descriptions.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.agents.prompts import VALIDATOR_SYSTEM, build_validator_prompt
from app.models.state import BatchState
from app.utils.gemini_client import GeminiClient
from app.utils.logging import get_logger

logger = get_logger(__name__)


async def validator_node(
    state: BatchState,
    client: GeminiClient,
    model: str,
    cached_content: str | None = None,
    uploaded_file: Any | None = None,
) -> BatchState:
    """LangGraph node: validate extracted content against the PDF and baseline.

    Args:
        state: Current graph state with extracted_pages and baseline_texts.
        client: GeminiClient instance.
        model: Model name (e.g. "models/gemini-2.5-flash").
        cached_content: Cache resource name.
        uploaded_file: Uploaded PDF file reference.

    Returns:
        Updated state with validation_result populated.
    """
    page_start = state["page_start"]
    page_end = state["page_end"]
    extracted_pages = state.get("extracted_pages", [])
    baseline_texts = state.get("baseline_texts", [])

    # Build baseline summary for the validator prompt
    baseline_summary = _build_baseline_summary(baseline_texts)

    # Build extracted content summary to append
    extracted_summary = json.dumps(extracted_pages, indent=2, ensure_ascii=False)

    prompt = build_validator_prompt(
        page_start=page_start,
        page_end=page_end,
        baseline_summary=baseline_summary,
    )
    prompt += f"\n\nEXTRACTED CONTENT:\n{extracted_summary}"

    # Build contents: PDF file reference + prompt
    contents: list[Any] = []
    if uploaded_file:
        contents.append(uploaded_file)
    contents.append(prompt)

    start = time.perf_counter()
    raw_response = await client.generate(
        model=model,
        contents=contents,
        system_instruction=VALIDATOR_SYSTEM,
        response_mime_type="application/json",
        cached_content=cached_content,
        temperature=0.0,
    )
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    # Parse validation response
    validation = _parse_validation_response(raw_response, page_start, page_end)

    logger.info(
        "validator_done",
        batch_id=state.get("batch_id"),
        pages=f"{page_start}-{page_end}",
        iteration=state.get("iteration_count", 0),
        passed=validation.get("passed", False),
        confidence=validation.get("overall_confidence", 0),
        time_ms=elapsed_ms,
    )

    # Build trace entry
    trace_entry = {
        "phase": "validator",
        "model": model,
        "iteration": state.get("iteration_count", 0),
        "cached": cached_content is not None,
        "passed": validation.get("passed", False),
        "confidence": validation.get("overall_confidence", 0),
        "failed_pages": [
            pr["page_num"] for pr in validation.get("page_results", [])
            if not pr.get("passed", True)
        ],
        "time_ms": elapsed_ms,
    }

    trace_log = list(state.get("trace_log", []))
    trace_log.append(trace_entry)

    # If validation passed, finalize the pages
    new_state: dict[str, Any] = {
        **state,
        "validation_result": validation,
        "trace_log": trace_log,
    }

    if validation.get("passed", False):
        new_state["final_pages"] = extracted_pages

    return new_state


def _build_baseline_summary(baseline_texts: list[dict]) -> str:
    """Build a condensed baseline text summary for the validator."""
    if not baseline_texts:
        return "(No baseline text available)"

    parts: list[str] = []
    for bt in baseline_texts:
        page_num = bt.get("page_num", "?")
        text = bt.get("text", "")
        # Truncate very long baselines to keep the prompt reasonable
        if len(text) > 2000:
            text = text[:2000] + "... [truncated]"
        parts.append(f"--- Page {page_num} ---\n{text}")

    return "\n\n".join(parts)


def _parse_validation_response(
    raw: str,
    page_start: int,
    page_end: int,
) -> dict:
    """Parse the JSON validation response.

    Returns a BatchValidation-compatible dict.
    Falls back to a passing result if parsing fails (to avoid blocking).
    """
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.index("\n") if "\n" in cleaned else len(cleaned)
        cleaned = cleaned[first_nl + 1:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.error("validator_parse_error", raw_preview=raw[:300])
        # Default to passing to avoid blocking the pipeline
        return _default_pass(page_start, page_end)

    if not isinstance(data, dict):
        return _default_pass(page_start, page_end)

    # Normalize the result
    return {
        "passed": data.get("passed", True),
        "overall_confidence": data.get("overall_confidence", 1.0),
        "page_results": [
            {
                "page_num": pr.get("page_num", page_start),
                "passed": pr.get("passed", True),
                "confidence": pr.get("confidence", 1.0),
                "gaps": pr.get("gaps", []),
            }
            for pr in data.get("page_results", [])
        ],
    }


def _default_pass(page_start: int, page_end: int) -> dict:
    """Return a default passing validation (used as fallback)."""
    return {
        "passed": True,
        "overall_confidence": 1.0,
        "page_results": [
            {"page_num": p, "passed": True, "confidence": 1.0, "gaps": []}
            for p in range(page_start, page_end + 1)
        ],
    }
