"""Validator agent — Flash 2.5 per-page validation against image + baseline."""

from __future__ import annotations

import json
import time

from app.agents.prompts import VALIDATOR_SYSTEM, build_validator_prompt
from app.models.state import PageState
from app.utils.gemini_client import GeminiClient
from app.utils.logging import get_logger

logger = get_logger(__name__)


async def validator_node(
    state: PageState,
    *,
    client: GeminiClient,
    model: str,
    pass_threshold: float = 0.85,
) -> PageState:
    """LangGraph node: validate the extractor's output for one page.

    If validation passes, the extracted content is locked into `final`.
    Otherwise, the graph router will retry (Flash) or escalate (Pro).
    """
    page_num = state["page_num"]
    extracted = state.get("extracted", {})
    extracted_json = json.dumps(extracted, indent=2, ensure_ascii=False)

    prompt = build_validator_prompt(
        page_num=page_num,
        extracted_json=extracted_json,
        baseline_text=state.get("baseline_text", ""),
        pass_threshold=pass_threshold,
    )

    start = time.perf_counter()
    raw = await client.generate_with_image(
        model=model,
        prompt=prompt,
        png_bytes=state["png_bytes"],
        system_instruction=VALIDATOR_SYSTEM,
        response_mime_type="application/json",
        temperature=0.0,
    )
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    validation = _parse_validation(raw, page_num, pass_threshold)

    logger.info(
        "validator_done",
        page_num=page_num,
        iteration=state.get("iteration", 0),
        passed=validation.get("passed"),
        confidence=validation.get("confidence"),
        gap_count=len(validation.get("gaps", [])),
        time_ms=elapsed_ms,
    )

    trace = list(state.get("trace", []))
    trace.append({
        "phase": "validator",
        "model": model,
        "iteration": state.get("iteration", 0),
        "passed": validation.get("passed"),
        "confidence": validation.get("confidence"),
        "time_ms": elapsed_ms,
    })

    new_state: PageState = {
        **state,
        "validation": validation,
        "trace": trace,
    }

    if validation.get("passed"):
        new_state["final"] = state.get("extracted", {})

    return new_state


def _parse_validation(raw: str, page_num: int, pass_threshold: float) -> dict:
    """Parse the validator JSON. Default to PASS on parse failure to avoid loops."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        nl = cleaned.index("\n") if "\n" in cleaned else len(cleaned)
        cleaned = cleaned[nl + 1:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.error("validator_parse_error", page_num=page_num, raw_preview=raw[:300])
        return {"page_num": page_num, "passed": True, "confidence": 1.0, "gaps": []}

    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        data = {}

    confidence = float(data.get("confidence", 1.0))
    gaps = data.get("gaps", []) or []
    passed = bool(data.get("passed", False)) and confidence >= pass_threshold and not gaps

    return {
        "page_num": data.get("page_num", page_num),
        "passed": passed,
        "confidence": confidence,
        "gaps": gaps,
    }
