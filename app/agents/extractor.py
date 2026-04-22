"""Extractor agent node — calls Gemini to extract content from PDF pages.

This is the primary extraction step.  It sends the cached PDF + page range
to Gemini Flash 2.5 with structured JSON output and returns per-page
extraction results (markdown, tables, images, cross-page notes).
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.agents.prompts import EXTRACTOR_SYSTEM, build_extractor_prompt
from app.models.schemas import PageExtraction
from app.models.state import BatchState
from app.utils.gemini_client import GeminiClient
from app.utils.logging import get_logger

logger = get_logger(__name__)


async def extractor_node(
    state: BatchState,
    client: GeminiClient,
    model: str,
    cached_content: str | None = None,
    uploaded_file: Any | None = None,
) -> BatchState:
    """LangGraph node: extract content from a batch of PDF pages.

    Args:
        state: Current graph state with batch identity and context.
        client: GeminiClient instance.
        model: Model name (e.g. "models/gemini-2.5-flash").
        cached_content: Cache resource name for cost savings.
        uploaded_file: Uploaded PDF file reference.

    Returns:
        Updated state with extracted_pages populated.
    """
    page_start = state["page_start"]
    page_end = state["page_end"]
    section_context = state.get("section_context", "")
    iteration = state.get("iteration_count", 0)

    # Build gap feedback from previous validation if this is a retry
    gap_feedback = ""
    if iteration > 0 and state.get("validation_result"):
        vr = state["validation_result"]
        failed_gaps = []
        for pr in vr.get("page_results", []):
            if not pr.get("passed", True) and pr.get("gaps"):
                failed_gaps.append(
                    f"Page {pr['page_num']}: {'; '.join(pr['gaps'])}"
                )
        gap_feedback = "\n".join(failed_gaps)

    # Build doc title from structure
    doc_title = ""
    ds = state.get("doc_structure", {})
    if ds:
        doc_title = ds.get("title", "")

    prompt = build_extractor_prompt(
        page_start=page_start,
        page_end=page_end,
        section_context=section_context,
        doc_title=doc_title,
        gap_feedback=gap_feedback,
    )

    # Build contents: PDF file reference + prompt
    contents: list[Any] = []
    if uploaded_file:
        contents.append(uploaded_file)
    contents.append(prompt)

    start = time.perf_counter()
    raw_response = await client.generate(
        model=model,
        contents=contents,
        system_instruction=EXTRACTOR_SYSTEM,
        response_mime_type="application/json",
        cached_content=cached_content,
        temperature=0.1,
    )
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    # Parse response
    extracted = _parse_extraction_response(raw_response, page_start, page_end)

    logger.info(
        "extractor_done",
        batch_id=state.get("batch_id"),
        pages=f"{page_start}-{page_end}",
        iteration=iteration,
        extracted_pages=len(extracted),
        time_ms=elapsed_ms,
    )

    # Build trace entry
    trace_entry = {
        "phase": "extractor",
        "model": model,
        "iteration": iteration,
        "pages": page_end - page_start + 1,
        "cached": cached_content is not None,
        "output_chars": len(raw_response),
        "time_ms": elapsed_ms,
    }

    trace_log = list(state.get("trace_log", []))
    trace_log.append(trace_entry)

    return {
        **state,
        "extracted_pages": extracted,
        "iteration_count": iteration + 1,
        "trace_log": trace_log,
    }


def _parse_extraction_response(
    raw: str,
    page_start: int,
    page_end: int,
) -> list[dict]:
    """Parse the JSON array response from the extractor.

    Returns a list of PageExtraction-compatible dicts.
    Falls back to a minimal result if parsing fails.
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
        logger.error("extractor_parse_error", raw_preview=raw[:300])
        # Return minimal fallback
        return [
            {"page_num": p, "markdown": "", "tables": [], "images": [], "cross_page_notes": ""}
            for p in range(page_start, page_end + 1)
        ]

    if not isinstance(data, list):
        data = [data]

    # Validate and normalize each page
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
