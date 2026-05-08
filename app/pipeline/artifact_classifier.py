"""Auto-classify a freshly ingested document into an artifact_class.

Runs after Phase 4 finishes (chunks/entities/images upserted) and only
when the user uploaded the document with the default ``artifact_class =
'other'``. A user-supplied class is always respected.

Inputs to the LLM:
  - filename
  - first ~10 markdown headings (page-tagged) from full_document.md
  - page_count, table_count, image_count
  - the first ~600 characters of full_document.md as a content sample

Output: one of ``app.domain.well_taxonomy.ARTIFACT_CLASSES``. On any
failure (Gemini error, malformed response, unknown value) the function
returns ``"other"`` — i.e. it never makes the row worse than the user
left it.

Best-effort and side-effect free; the orchestrator is responsible for
persisting the result via UPDATE.
"""

from __future__ import annotations

import json
from typing import Any

from google import genai
from google.genai import types

from app.config import Settings
from app.domain.well_taxonomy import ARTIFACT_CLASSES
from app.utils.logging import get_logger

logger = get_logger(__name__)


_CLASSIFIER_SYSTEM = """\
You classify drilling / well-operations PDF documents into ONE of these \
artifact classes (PRD §10 evidence hierarchy):

  master    — Approved drilling program, AFE, master plan
  program   — Well program / engineering design / well-construction plan / EOWR / WCR / final well report
  offset    — Offset-well report / analog-well study
  ddr       — Daily Drilling Report (one operational day)
  handover  — Shift / tour / section handover note
  service   — Service-company report (mud, MWD, LWD, DD, cement, wireline)
  risk      — Risk register, HAZOP, hazard / assurance memo, bowtie
  action    — Action items / punch list / decision log
  learning  — Lessons learned, post-mortem, knowledge capture
  media     — Standalone photos, screenshots, image-only attachments
  other     — Use ONLY if none of the above clearly fit

Pick the BEST single match based on filename, headings, and content sample. \
Be decisive — if a document looks like a daily report, return "ddr"; if a \
plan, return "program"; etc. Return "other" only when the document is \
genuinely outside the taxonomy.

Respond with a JSON object only:
  {"artifact_class": "<one of the values above>", "confidence": <0.0-1.0>}

No prose, no markdown, no code fences.
"""


_MAX_HEADINGS = 10
_MAX_CONTENT_SAMPLE = 600
_MAX_OUTPUT_TOKENS = 64


def _format_outline_for_prompt(
    *,
    filename: str,
    headings: list[dict[str, Any]],
    page_count: int | None,
    table_count: int,
    image_count: int,
    content_sample: str,
) -> str:
    head_lines = []
    for h in headings[:_MAX_HEADINGS]:
        page = h.get("page")
        text = (h.get("text") or "").strip()
        if not text:
            continue
        prefix = f"  p.{page}: " if page is not None else "  "
        head_lines.append(f"{prefix}{text}")
    head_block = "\n".join(head_lines) if head_lines else "  (no headings parsed)"

    sample = (content_sample or "").strip().replace("\r", "")
    if len(sample) > _MAX_CONTENT_SAMPLE:
        sample = sample[:_MAX_CONTENT_SAMPLE].rstrip() + " …"
    if not sample:
        sample = "(empty)"

    return (
        f"filename: {filename}\n"
        f"page_count: {page_count if page_count is not None else 'unknown'}\n"
        f"table_count: {table_count}\n"
        f"image_count: {image_count}\n"
        f"top headings:\n{head_block}\n\n"
        f"content sample (first {_MAX_CONTENT_SAMPLE} chars):\n{sample}"
    )


def _parse_response(raw: str) -> tuple[str, float]:
    """Return ``(artifact_class, confidence)``.

    Falls back to ``("other", 0.0)`` if the response is unparseable or
    yields a class outside the controlled vocabulary.
    """
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        nl = cleaned.find("\n")
        cleaned = cleaned[nl + 1:] if nl != -1 else cleaned
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return "other", 0.0
    if not isinstance(data, dict):
        return "other", 0.0

    cls = str(data.get("artifact_class", "")).strip().lower()
    if cls not in ARTIFACT_CLASSES:
        return "other", 0.0

    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    return cls, conf


async def classify_artifact(
    *,
    filename: str,
    headings: list[dict[str, Any]],
    page_count: int | None,
    table_count: int,
    image_count: int,
    content_sample: str,
    settings: Settings,
    client: genai.Client | None = None,
) -> tuple[str, float]:
    """Classify one document. Returns ``(artifact_class, confidence)``.

    Never raises — on any error returns ``("other", 0.0)``.
    """
    cli = client or genai.Client(api_key=settings.gemini_api_key)

    prompt = _format_outline_for_prompt(
        filename=filename,
        headings=headings,
        page_count=page_count,
        table_count=table_count,
        image_count=image_count,
        content_sample=content_sample,
    )

    try:
        resp = await cli.aio.models.generate_content(
            model=settings.chat_synth_model,
            contents=[types.Content(
                role="user", parts=[types.Part.from_text(text=prompt)],
            )],
            config=types.GenerateContentConfig(
                system_instruction=_CLASSIFIER_SYSTEM,
                temperature=0.0,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
                response_mime_type="application/json",
            ),
        )
        raw = (getattr(resp, "text", "") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("artifact_classify_failed", filename=filename, error=str(exc))
        return "other", 0.0

    cls, conf = _parse_response(raw)
    logger.info(
        "artifact_classified",
        filename=filename,
        artifact_class=cls,
        confidence=conf,
    )
    return cls, conf
