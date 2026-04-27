"""Per-page LangGraph: extractor → validator → (pass | retry | escalate).

Iteration cap = 3. iter1 & iter2 use Gemini 3 Flash Preview; iter3 uses Gemini 3.1 Pro Preview
(escalation node, FINAL — no further validation). One graph instance
is compiled once and invoked per page; 100 invocations run concurrently.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.escalation import escalation_node
from app.agents.extractor import extractor_node
from app.agents.validator import validator_node
from app.config import Settings
from app.models.schemas import PageImage
from app.models.state import PageState
from app.utils.gemini_client import GeminiClient
from app.utils.logging import get_logger

logger = get_logger(__name__)


def build_graph(client: GeminiClient, settings: Settings) -> Any:
    """Build & compile the per-page LangGraph.

    Args:
        client: Singleton GeminiClient (shared by all workers).
        settings: Application settings.

    Returns:
        Compiled LangGraph ready for `.ainvoke(state)`.
    """
    extraction_model = settings.extraction_model
    escalation_model = settings.escalation_model
    max_iterations = settings.max_iterations
    pass_threshold = settings.pass_confidence_threshold

    async def _extractor(state: PageState) -> PageState:
        return await extractor_node(state, client=client, model=extraction_model)

    async def _validator(state: PageState) -> PageState:
        return await validator_node(
            state,
            client=client,
            model=extraction_model,
            pass_threshold=pass_threshold,
        )

    async def _escalation(state: PageState) -> PageState:
        return await escalation_node(state, client=client, model=escalation_model)

    def _after_validator(state: PageState) -> str:
        """Route: pass → END, iter < max-1 → retry, else → escalate (Pro FINAL)."""
        validation = state.get("validation", {})
        if validation.get("passed"):
            return "pass"

        iteration = state.get("iteration", 0)
        # iter 1 done -> retry (iter 2 = Flash + gaps)
        # iter 2 done -> escalate (iter 3 = Pro FINAL)
        if iteration < max_iterations - 1:
            return "retry"
        return "escalate"

    graph = StateGraph(PageState)
    graph.add_node("extractor", _extractor)
    graph.add_node("validator", _validator)
    graph.add_node("escalation", _escalation)

    graph.set_entry_point("extractor")
    graph.add_edge("extractor", "validator")
    graph.add_conditional_edges(
        "validator",
        _after_validator,
        {"pass": END, "retry": "extractor", "escalate": "escalation"},
    )
    graph.add_edge("escalation", END)

    return graph.compile()


def page_image_to_state(page: PageImage) -> PageState:
    """Build the initial PageState for a page about to enter the graph."""
    return PageState(
        page_num=page.page_num,
        png_bytes=page.png_bytes,
        is_scanned=page.is_scanned,
        baseline_text=page.baseline_text,
        extracted={},
        validation={},
        iteration=0,
        escalated=False,
        final={},
        trace=[],
    )
