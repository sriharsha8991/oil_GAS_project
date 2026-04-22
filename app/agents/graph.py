"""LangGraph workflow — wires extractor → validator → conditional retry/escalation.

Graph flow:
    START → extractor → validator → CONDITIONAL
                                       │
                          ┌────────────┼────────────┐
                          │            │            │
                       PASS        RETRY         ESCALATE
                        │        (≤ max)        (Pro 2.5)
                        ▼            │            ▼
                       END      extractor        END

The graph is compiled once and invoked per-batch with concurrency
controlled by an asyncio.Semaphore in the orchestrator.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.escalation import escalation_node
from app.agents.extractor import extractor_node
from app.agents.validator import validator_node
from app.config import Settings
from app.models.schemas import PageBatch
from app.models.state import BatchState
from app.utils.gemini_client import GeminiClient
from app.utils.logging import get_logger

logger = get_logger(__name__)


def build_graph(
    client: GeminiClient,
    settings: Settings,
    cached_content: str | None = None,
    uploaded_file: Any | None = None,
) -> Any:
    """Build and compile the LangGraph extraction pipeline.

    Args:
        client: Shared GeminiClient with rate limiting.
        settings: Application settings (model names, max_retries).
        cached_content: Gemini cache resource name (from Phase 1).
        uploaded_file: Uploaded PDF file reference (from Phase 1).

    Returns:
        Compiled LangGraph ready for .ainvoke(state).
    """
    extraction_model = settings.extraction_model
    escalation_model = settings.escalation_model
    max_retries = settings.max_retries

    # --- Node wrappers (bind runtime deps into closures) ---

    async def _extractor(state: BatchState) -> BatchState:
        return await extractor_node(
            state,
            client=client,
            model=extraction_model,
            cached_content=cached_content,
            uploaded_file=uploaded_file,
        )

    async def _validator(state: BatchState) -> BatchState:
        return await validator_node(
            state,
            client=client,
            model=extraction_model,
            cached_content=cached_content,
            uploaded_file=uploaded_file,
        )

    async def _escalation(state: BatchState) -> BatchState:
        return await escalation_node(
            state,
            client=client,
            model=escalation_model,
            uploaded_file=uploaded_file,
        )

    # --- Conditional routing after validator ---

    def _after_validator(state: BatchState) -> str:
        """Decide: pass → end, retry → extractor, escalate → escalation."""
        vr = state.get("validation_result", {})
        passed = vr.get("passed", False)

        if passed:
            return "pass"

        iteration = state.get("iteration_count", 0)
        if iteration < max_retries:
            return "retry"

        return "escalate"

    # --- Build graph ---

    graph = StateGraph(BatchState)

    graph.add_node("extractor", _extractor)
    graph.add_node("validator", _validator)
    graph.add_node("escalation", _escalation)

    graph.set_entry_point("extractor")
    graph.add_edge("extractor", "validator")

    graph.add_conditional_edges(
        "validator",
        _after_validator,
        {
            "pass": END,
            "retry": "extractor",
            "escalate": "escalation",
        },
    )

    graph.add_edge("escalation", END)

    return graph.compile()


def batch_to_state(batch: PageBatch, doc_structure_dict: dict) -> BatchState:
    """Convert a PageBatch + DocStructure dict into the initial BatchState."""
    return BatchState(
        batch_id=batch.batch_id,
        page_start=batch.page_start,
        page_end=batch.page_end,
        section_context=batch.section_context,
        doc_structure=doc_structure_dict,
        baseline_texts=[bt.model_dump() for bt in batch.baseline_texts],
        media_resolution=batch.media_resolution,
        extracted_pages=[],
        validation_result={},
        iteration_count=0,
        escalated=False,
        final_pages=[],
        trace_log=[],
    )


async def run_graph_on_batches(
    graph: Any,
    batches: list[PageBatch],
    doc_structure_dict: dict,
    max_concurrent: int = 25,
) -> list[BatchState]:
    """Run the compiled graph on all batches with concurrency control.

    Args:
        graph: Compiled LangGraph.
        batches: List of PageBatch from Phase 2.
        doc_structure_dict: DocStructure as dict.
        max_concurrent: Max parallel batch invocations.

    Returns:
        List of completed BatchState results, one per batch.
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    results: list[BatchState | None] = [None] * len(batches)

    async def _process(index: int, batch: PageBatch) -> None:
        async with semaphore:
            initial_state = batch_to_state(batch, doc_structure_dict)
            try:
                result = await graph.ainvoke(initial_state)
                results[index] = result
                logger.info(
                    "batch_complete",
                    batch_id=batch.batch_id,
                    pages=f"{batch.page_start}-{batch.page_end}",
                    escalated=result.get("escalated", False),
                )
            except Exception as exc:
                logger.error(
                    "batch_failed",
                    batch_id=batch.batch_id,
                    pages=f"{batch.page_start}-{batch.page_end}",
                    error=str(exc),
                )
                # Store a minimal failed state
                results[index] = BatchState(
                    batch_id=batch.batch_id,
                    page_start=batch.page_start,
                    page_end=batch.page_end,
                    final_pages=[],
                    trace_log=[{"phase": "error", "error": str(exc)}],
                )

    tasks = [_process(i, batch) for i, batch in enumerate(batches)]
    await asyncio.gather(*tasks)

    return [r for r in results if r is not None]
