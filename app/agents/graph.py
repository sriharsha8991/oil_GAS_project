"""Per-page LangGraph: extractor (self-validating) → (END | escalation → END).

Two-stage flow:
  1. Extractor (Flash) extracts AND self-reports confidence + gaps in one call.
  2. If confidence >= pass_threshold AND gaps is empty: END (single LLM call).
  3. Otherwise: Escalation (Pro) re-extracts with the gaps + previous confidence
     as targeted feedback. FINAL — no further validation.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.escalation import escalation_node
from app.agents.extractor import extractor_node
from app.config import Settings
from app.models.schemas import PageImage
from app.models.state import PageState
from app.utils.gemini_client import GeminiClient
from app.utils.logging import get_logger

logger = get_logger(__name__)


def build_graph(client: GeminiClient, settings: Settings) -> Any:
    """Build & compile the per-page LangGraph."""
    extraction_model = settings.extraction_model
    escalation_model = settings.escalation_model
    pass_threshold = settings.pass_confidence_threshold

    async def _extractor(state: PageState) -> PageState:
        return await extractor_node(
            state,
            client=client,
            model=extraction_model,
            pass_threshold=pass_threshold,
        )

    async def _escalation(state: PageState) -> PageState:
        return await escalation_node(state, client=client, model=escalation_model)

    def _after_extractor(state: PageState) -> str:
        validation = state.get("validation", {})
        return "pass" if validation.get("passed") else "escalate"

    graph = StateGraph(PageState)
    graph.add_node("extractor", _extractor)
    graph.add_node("escalation", _escalation)

    graph.set_entry_point("extractor")
    graph.add_conditional_edges(
        "extractor",
        _after_extractor,
        {"pass": END, "escalate": "escalation"},
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
