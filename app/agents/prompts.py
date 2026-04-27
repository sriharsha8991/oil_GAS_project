"""System prompts and prompt builders for the per-page extraction pipeline.

All prompts are centralised here so they can be tuned independently
of the agent logic.  Each builder takes runtime context (page number,
gap feedback) and returns a ready-to-send user message paired with
an inline image.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Extractor (Gemini 3 Flash Preview — iter 1 & 2)
# ---------------------------------------------------------------------------

EXTRACTOR_SYSTEM = (
    "You are a specialist document extraction agent for geological well reports. "
    "You are given ONE rendered page image of a PDF. Extract ALL content from "
    "that single page into clean, accurate Markdown. Preserve exact numbers, "
    "depths, coordinates, dates, well names, and technical terminology. "
    "Never invent or hallucinate content. If you cannot read something, omit "
    "it rather than guess."
)


def build_extractor_prompt(
    page_num: int,
    *,
    is_scanned: bool = False,
    baseline_text: str = "",
    gap_feedback: str = "",
) -> str:
    """Build the user-message prompt for the extractor agent.

    Args:
        page_num: 0-indexed page number (used for the JSON `page_num` field).
        is_scanned: True if PyMuPDF found <50 chars on this page.
        baseline_text: PyMuPDF baseline (passed only as a hint, NOT ground truth).
        gap_feedback: From validator, only set on iter 2.
    """
    scan_hint = (
        "\nThis page appears to be scanned (no text layer). Rely on vision."
        if is_scanned
        else ""
    )

    baseline_hint = ""
    if baseline_text and not is_scanned:
        snippet = baseline_text[:1500]
        if len(baseline_text) > 1500:
            snippet += " ... [truncated]"
        baseline_hint = (
            "\n\n--- HINT: PyMuPDF baseline text for this page ---\n"
            f"{snippet}\n"
            "--- END HINT ---\n"
            "Use this only as a hint; the page IMAGE is the source of truth."
        )

    retry_block = ""
    if gap_feedback:
        retry_block = (
            "\n\n--- RETRY: previous extraction had gaps ---\n"
            f"{gap_feedback}\n"
            "Re-extract the page and FIX these specific issues.\n"
            "--- END RETRY ---"
        )

    return f"""\
Extract ALL content from page {page_num} of the attached image into Markdown.{scan_hint}

Return a JSON object with this exact schema:
{{
  "page_num": {page_num},
  "markdown": "<full page content as clean Markdown>",
  "tables": [
    {{
      "id": "t1",
      "markdown": "<Markdown table>",
      "json_data": {{"headers": [...], "rows": [[...], ...]}},
      "continues_on_next_page": false,
      "bbox": [ymin, xmin, ymax, xmax]
    }}
  ],
  "images": [
    {{
      "id": "img1",
      "description": "<what the image/diagram/chart shows>",
      "location": "<position: top/center/bottom/full-page>",
      "image_type": "<log_strip|schematic|plot|photo|table_image|other>",
      "bbox": [ymin, xmin, ymax, xmax]
    }}
  ],
  "entities": [
    {{
      "type": "<well_id|depth|formation|npt_event|fluid|bha|date|other>",
      "value": "<canonical text, e.g. '3450' or 'Eagle Ford' or 'wait_on_weather'>",
      "unit": "<ft|m|ppg|psi|hours|usd|...>  (empty if N/A)",
      "context": "<short surrounding phrase to disambiguate>",
      "bbox": [ymin, xmin, ymax, xmax]
    }}
  ],
  "cross_page_notes": "<note if a table or section appears to continue from prev or to next page, else empty string>"
}}

Bounding-box rules (CRITICAL):
- All `bbox` arrays use Gemini's normalized format: [ymin, xmin, ymax, xmax] each in 0-1000.
- Origin (0,0) is top-left. Always include bbox for tables, images, and entities.
- Omit `bbox` only if the element occupies the full page or is non-localizable.

Entity rules:
- Extract well IDs (API numbers, well names), depths (with unit AND type if known: MD/TVD),
  formation tops, NPT events (with hours + cause if visible), fluids (mud weight, etc),
  BHA components, dates. Do NOT invent.
- Free-text categories acceptable in this version (controlled vocab in next release).

General rules:
- Extract EVERY piece of text visible on the page.
- Reproduce tables exactly — all rows, all columns, all numbers.
- Describe every image, diagram, chart, well log, or figure.
- Set `continues_on_next_page=true` for tables that clearly run off the bottom.
- Use proper Markdown: headings (#, ##), bold (**), lists (-, 1.), tables (|).
- Return ONLY the JSON object, no extra text, no markdown code fences.{baseline_hint}{retry_block}"""


# ---------------------------------------------------------------------------
# Validator (Gemini 3 Flash Preview — always)
# ---------------------------------------------------------------------------

VALIDATOR_SYSTEM = (
    "You are a quality assurance agent for document extraction. "
    "You are given ONE rendered page image, the PyMuPDF baseline text for "
    "that page, and the extractor's output. Your job is to find gaps, "
    "errors, and missing content — not to re-extract. Be strict."
)


def build_validator_prompt(
    page_num: int,
    extracted_json: str,
    baseline_text: str,
    *,
    pass_threshold: float = 0.85,
) -> str:
    """Build the user-message prompt for the validator agent."""
    baseline_snippet = baseline_text[:2000]
    if len(baseline_text) > 2000:
        baseline_snippet += " ... [truncated]"

    return f"""\
Validate the extraction of page {page_num} from the attached page image.

You have THREE sources:
1. **Page image** (attached above) — the GROUND TRUTH.
2. **PyMuPDF baseline text** (independent extraction):
{baseline_snippet}
--- END BASELINE ---

3. **Extracted content** (the extractor's output, JSON):
{extracted_json}
--- END EXTRACTED ---

Evaluate on:
- **Text completeness**: Is ALL text visible on the page in the extraction?
  Cross-check the baseline — if baseline contains text the extraction lacks
  AND the image confirms that text exists, flag it.
- **Table integrity**: Are ALL rows and columns present? Do numbers match?
- **Image/diagram coverage**: Is every visual element described?
- **Factual accuracy**: Do extracted numbers, names, depths match the image?
- **Hallucination**: Is there any content in the extraction that does NOT
  exist on the page? Flag immediately.

Return a JSON object:
{{
  "page_num": {page_num},
  "passed": <bool>,
  "confidence": <float 0.0-1.0>,
  "gaps": ["<specific, actionable gap description>", ...]
}}

Rules:
- `passed` = true ONLY if confidence >= {pass_threshold} AND `gaps` is empty.
- Be specific: quote the missing text, name the missing column, etc.
- Return ONLY the JSON object, no extra text, no code fences."""


# ---------------------------------------------------------------------------
# Escalator (Gemini 3.1 Pro Preview — iter 3, FINAL)
# ---------------------------------------------------------------------------

ESCALATION_SYSTEM = (
    "You are an expert document extraction agent using an advanced model. "
    "You handle pages a faster model could not extract correctly: "
    "handwritten annotations, extremely dense tables, overlapping layouts, "
    "degraded scans. Extract with maximum accuracy. This is the FINAL attempt."
)


def build_escalation_prompt(
    page_num: int,
    *,
    is_scanned: bool = False,
    baseline_text: str = "",
    previous_gaps: str = "",
) -> str:
    """Build the prompt for the Gemini 3.1 Pro Preview escalation agent (iter 3)."""
    scan_hint = (
        "\nThis page appears to be scanned (no text layer). Rely on vision."
        if is_scanned
        else ""
    )

    baseline_hint = ""
    if baseline_text and not is_scanned:
        snippet = baseline_text[:1500]
        if len(baseline_text) > 1500:
            snippet += " ... [truncated]"
        baseline_hint = (
            "\n\n--- PyMuPDF baseline text (hint) ---\n"
            f"{snippet}\n"
            "--- END HINT ---"
        )

    gaps_block = (
        f"\n\n--- Previous gaps from iter 1 & 2 ---\n{previous_gaps}\n--- END GAPS ---"
        if previous_gaps
        else ""
    )

    return f"""\
Re-extract page {page_num} from the attached image. Two prior Flash attempts \
failed validation.{scan_hint}{gaps_block}

Return the SAME JSON schema as the extractor (with bbox + entities):
{{
  "page_num": {page_num},
  "markdown": "<full page content as clean Markdown>",
  "tables":   [{{"id":"t1","markdown":"...","json_data":{{...}},"continues_on_next_page":false,"bbox":[ymin,xmin,ymax,xmax]}}],
  "images":   [{{"id":"img1","description":"...","location":"...","image_type":"...","bbox":[ymin,xmin,ymax,xmax]}}],
  "entities": [{{"type":"...","value":"...","unit":"...","context":"...","bbox":[ymin,xmin,ymax,xmax]}}],
  "cross_page_notes": "<if applicable>"
}}

Rules:
- This is the FINAL attempt — extract with maximum accuracy.
- Address every flagged gap above.
- bbox arrays are [ymin, xmin, ymax, xmax] normalized 0-1000 (Gemini standard).
- Return ONLY the JSON object, no extra text, no code fences.{baseline_hint}"""
