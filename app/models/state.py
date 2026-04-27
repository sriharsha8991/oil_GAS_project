"""LangGraph state definitions for the per-page agent pipeline."""

from __future__ import annotations

from typing import TypedDict


class PageState(TypedDict, total=False):
    """State that flows through the per-page LangGraph.

    One PageState instance exists per PDF page; 100 instances run
    concurrently across the worker pool. Every field is optional
    (total=False) so nodes populate them incrementally.
    """

    # --- Identity & inputs ---
    page_num: int
    png_bytes: bytes              # The rendered page (inline image)
    is_scanned: bool
    baseline_text: str            # Phase 0 reference for the validator

    # --- Extractor output (replaced each iteration) ---
    extracted: dict               # PageExtraction-shaped dict

    # --- Validator output ---
    validation: dict              # PageValidation-shaped dict

    # --- Control flow ---
    iteration: int                # 1 = Flash, 2 = Flash + gaps, 3 = Pro (FINAL)
    escalated: bool               # True if iter 3 ran
    final: dict                   # Locked-in extraction (passed or escalated)

    # --- Observability ---
    trace: list[dict]
