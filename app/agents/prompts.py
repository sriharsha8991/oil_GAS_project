"""System prompts and prompt builders for the extraction pipeline agents.

All prompts are centralised here so they can be tuned independently
of the agent logic.  Each builder takes runtime context (section name,
page range, gap feedback) and returns a ready-to-send user message.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

EXTRACTOR_SYSTEM = (
    "You are a specialist document extraction agent for geological well reports. "
    "Your job is to extract ALL content from the given PDF pages into clean, "
    "accurate Markdown.  Preserve exact numbers, depths, coordinates, dates, "
    "and technical terminology.  Never invent or hallucinate content."
)

def build_extractor_prompt(
    page_start: int,
    page_end: int,
    section_context: str,
    doc_title: str = "",
    gap_feedback: str = "",
) -> str:
    """Build the user-message prompt for the extractor agent."""
    pages_desc = (
        f"page {page_start}" if page_start == page_end
        else f"pages {page_start}–{page_end}"
    )
    section_line = f'These pages belong to the "{section_context}" section.' if section_context else ""
    doc_line = f"Document: {doc_title}." if doc_title else ""

    retry_block = ""
    if gap_feedback:
        retry_block = (
            "\n\n--- RETRY INSTRUCTIONS ---\n"
            "Your previous extraction had gaps. Focus on fixing these specific issues:\n"
            f"{gap_feedback}\n"
            "Re-extract ONLY the pages with issues, keeping existing correct content.\n"
            "--- END RETRY ---"
        )

    return f"""\
Extract ALL content from {pages_desc} of the attached PDF into Markdown.
{doc_line}
{section_line}

Return a JSON array with one object per page. Each object must have:
{{
  "page_num": <int, 0-indexed>,
  "markdown": "<full page content as clean Markdown>",
  "tables": [
    {{
      "id": "t1",
      "markdown": "<Markdown table>",
      "json_data": {{"headers": [...], "rows": [[...], ...]}},
      "continues_on_next_page": false
    }}
  ],
  "images": [
    {{
      "id": "img1",
      "description": "<what the image/diagram shows>",
      "location": "<position on page: top/center/bottom>"
    }}
  ],
  "cross_page_notes": "<if a table or section continues from/to another page>"
}}

Rules:
- Extract EVERY piece of text visible on each page.
- Reproduce tables exactly — all rows, all columns, all numbers.
- Describe every image, diagram, chart, or figure.
- Note any content that continues across pages.
- Use proper Markdown: headings (#, ##), bold (**), lists (-, 1.), tables (|).
- Return ONLY the JSON array, no extra text.
{retry_block}"""


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

VALIDATOR_SYSTEM = (
    "You are a quality assurance agent for document extraction. "
    "You compare extracted content against the original PDF pages AND an "
    "independent baseline text extraction.  Your job is to find gaps, "
    "errors, and missing content — not to re-extract."
)

def build_validator_prompt(
    page_start: int,
    page_end: int,
    baseline_summary: str,
) -> str:
    """Build the user-message prompt for the validator agent."""
    pages_desc = (
        f"page {page_start}" if page_start == page_end
        else f"pages {page_start}–{page_end}"
    )

    return f"""\
Validate the extraction of {pages_desc} from the attached PDF.

You have THREE sources of truth:
1. **PDF pages** (attached) — the ground truth visual.
2. **Baseline text** — an independent pymupdf4llm extraction (below).
3. **Extracted content** — the extraction to validate (below).

BASELINE TEXT (independent extraction):
{baseline_summary}

--- END BASELINE ---

The extracted content follows after this prompt.

Evaluate each page on:
- **Text completeness**: Does the extraction contain ALL text visible on the page?
  Cross-check against the baseline — if baseline has text the extraction lacks, flag it.
- **Table integrity**: Are ALL rows and columns present? Do numbers match exactly?
- **Image/diagram coverage**: Is every visual element described?
- **Cross-page coherence**: Do continued tables/sections connect properly?
- **Factual accuracy**: Do extracted numbers, names, depths match the PDF?

Return a JSON object:
{{
  "passed": <bool — true only if ALL pages pass>,
  "overall_confidence": <float 0.0–1.0>,
  "page_results": [
    {{
      "page_num": <int>,
      "passed": <bool>,
      "confidence": <float 0.0–1.0>,
      "gaps": ["<specific gap description>", ...]
    }}
  ]
}}

Rules:
- Be strict: flag ANY missing text, table rows, or unreported images.
- Be specific in gaps: quote the missing content or describe exactly what's wrong.
- Confidence below 0.8 means the page should NOT pass.
- Return ONLY the JSON object, no extra text."""


# ---------------------------------------------------------------------------
# Escalation (Pro model)
# ---------------------------------------------------------------------------

ESCALATION_SYSTEM = (
    "You are an expert document extraction agent using an advanced model. "
    "You handle edge cases that a standard model could not resolve: "
    "handwritten annotations, extremely dense tables, overlapping layouts, "
    "and degraded scanned text.  Extract with maximum accuracy."
)

def build_escalation_prompt(
    page_start: int,
    page_end: int,
    section_context: str,
    previous_gaps: str,
) -> str:
    """Build the prompt for the Pro 2.5 escalation agent."""
    pages_desc = (
        f"page {page_start}" if page_start == page_end
        else f"pages {page_start}–{page_end}"
    )

    return f"""\
Re-extract {pages_desc} from the attached PDF.  Previous extraction attempts \
failed validation.  Focus on these specific issues:

{previous_gaps}

Section context: "{section_context}"

Return a JSON array with one object per page, same schema as before:
{{
  "page_num": <int, 0-indexed>,
  "markdown": "<full page content as clean Markdown>",
  "tables": [...],
  "images": [...],
  "cross_page_notes": "<if applicable>"
}}

Rules:
- This is the FINAL attempt — extract with maximum accuracy.
- Pay special attention to the flagged gaps above.
- Return ONLY the JSON array, no extra text."""
