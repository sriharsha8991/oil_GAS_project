"""LangGraph state definitions for the agent pipeline."""

from __future__ import annotations

from typing import Any, TypedDict


class BatchState(TypedDict, total=False):
    """State that flows through the LangGraph extractor → validator graph.

    Every field is optional (total=False) so nodes can populate them
    incrementally as the graph executes.
    """

    # --- Identity ---
    batch_id: int
    page_start: int
    page_end: int

    # --- Context from earlier phases ---
    section_context: str              # Section name from DocStructure
    doc_structure: dict[str, Any]     # Full DocStructure dict
    baseline_texts: list[dict]        # List of PageBaseline dicts
    media_resolution: str             # "HIGH" | "MEDIUM"

    # --- Extractor output ---
    extracted_pages: list[dict]       # List of PageExtraction dicts

    # --- Validator output ---
    validation_result: dict           # BatchValidation dict

    # --- Control flow ---
    iteration_count: int              # Retry count (max 2)
    escalated: bool

    # --- Final ---
    final_pages: list[dict]           # Final PageExtraction dicts after all retries

    # --- Observability ---
    trace_log: list[dict]
