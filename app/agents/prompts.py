"""System prompts and prompt builders for the per-page extraction pipeline.

Two-stage flow (no separate validator):
  1. Extractor (Gemini 3 Flash Preview) — extracts AND self-reports
     confidence + remaining gaps in the same JSON response.
  2. Escalator (Gemini 3.1 Pro Preview) — runs only when stage 1
     self-reported low confidence or non-empty gaps. Receives the
     extractor's gaps + confidence as targeted feedback.
"""

from __future__ import annotations

from app.domain.well_taxonomy import entity_type_hints


# ---------------------------------------------------------------------------
# Extractor (Gemini 3 Flash Preview — self-validating, single shot)
# ---------------------------------------------------------------------------

EXTRACTOR_SYSTEM = (
    "You are a specialist document extraction agent for geological well reports. "
    "You are given ONE rendered page image of a PDF. Extract ALL content from "
    "that single page into clean, accurate Markdown. Preserve exact numbers, "
    "depths, coordinates, dates, well names, and technical terminology. "
    "Never invent or hallucinate content. If you cannot read something, omit "
    "it rather than guess.\n\n"
    "You are also responsible for HONEST self-assessment. After extracting, "
    "critically review your own output against the page image and report:\n"
    "  - `self_confidence` (0.0-1.0): how complete & accurate the extraction is.\n"
    "  - `self_gaps`: a list of specific things you could NOT extract reliably "
    "(unreadable text, dense tables, handwritten notes, ambiguous diagrams). "
    "If everything is clean, return an empty list.\n"
    "Be strict — under-confidence is preferred over over-confidence. A more "
    "powerful model will be invoked when you flag gaps, so flag them honestly.\n\n"
    "When extracting entities, you MUST use the controlled vocabulary below. "
    "Pick the closest matching value from the closed lists; do NOT invent "
    "new category strings.\n\n"
    + entity_type_hints()
)


def build_extractor_prompt(
    page_num: int,
    *,
    is_scanned: bool = False,
    baseline_text: str = "",
    pass_threshold: float = 0.85,
) -> str:
    """Build the user-message prompt for the extractor agent."""
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

    return f"""\
Extract ALL content from page {page_num} of the attached image into Markdown,
THEN self-assess your work.{scan_hint}

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
      "description": "<exhaustive, structured description — see Image description rules below>",
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
  "cross_page_notes": "<note if a table or section appears to continue from prev or to next page, else empty string>",
  "self_confidence": <float 0.0-1.0>,
  "self_gaps": ["<specific, actionable description of anything you could NOT extract reliably>", ...]
}}

Bounding-box rules (CRITICAL):
- All `bbox` arrays use Gemini's normalized format: [ymin, xmin, ymax, xmax] each in 0-1000.
- Origin (0,0) is top-left. Always include bbox for tables, images, and entities.
- Omit `bbox` only if the element occupies the full page or is non-localizable.

Entity rules:
- Extract well IDs (API numbers, well names), depths (with unit AND type: MD/TVD/KB/RT/MSL/AHBDF/RKB/BRT/SS),
  formation tops, NPT events (with hours + IADC cause category), fluids (mud weight, etc),
  BHA components, dates, operations, pressures, temperatures, casing, bits, rigs.
- For entity `type`, pick from: well_id, depth, formation, npt_event, fluid, bha, date,
  operation, pressure, temperature, casing, bit, mud_weight, rig, personnel, equipment, other.
- For NPT events, map to IADC categories: mechanical_failure, stuck_pipe, lost_circulation,
  well_control, wait_on_weather, wait_on_cement, wait_on_equipment, wait_on_orders,
  fishing, reaming, twist_off, bha_failure, mwd_lwd_failure, directional_issues, hole_condition, other.
- For units, use standard abbreviations: ft, m, psi, kPa, ppg, kg/m3, bbl, F, C, hours, in, mm.
- Do NOT invent entity values. Only extract what is clearly visible on the page.

General rules:
- Extract EVERY piece of text visible on the page.
- Reproduce tables exactly — all rows, all columns, all numbers.
- Describe every image, diagram, chart, well log, or figure.
- Set `continues_on_next_page=true` for tables that clearly run off the bottom.
- Use proper Markdown: headings (#, ##), bold (**), lists (-, 1.), tables (|).

Image filtering rules (CRITICAL — reduce noise):
- ONLY include images in the `images` array if they carry information that
  matters for understanding the well report.
- INCLUDE: well log strips, well schematics, formation cross-sections, drilling
  diagrams, BHA diagrams, casing diagrams, mud logs, deviation plots, pressure
  plots, depth plots, geological maps, location maps, photographs of cores or
  cuttings, charts/graphs with labelled axes or data, and any technical figure
  whose content is referenced by the surrounding text.
- SKIP (do NOT add to `images`): company logos, page-corner branding, decorative
  borders, signature blocks, document watermarks, page-number ornaments, generic
  letterhead graphics, social-media icons, QR codes that only point to a website,
  and any purely decorative element that adds no domain content.
- When in doubt, ask: "Would a geologist or drilling engineer ever ask about
  this image?" If no, skip it.

Image description rules (CRITICAL — the description IS the searchable record):
- Treat each kept image as if the reader will NEVER see the picture. Capture
  every fact a downstream RAG query could possibly need.
- Always start with one short sentence stating WHAT the figure is and WHY it
  matters (e.g. "Wellbore schematic for Well X showing casing program from
  surface to TD").
- Then enumerate, line by line (use `\n` inside the JSON string), every
  domain-relevant detail you can read off the page:
    • Title, caption, figure number, legend entries, scale bars, north arrow.
    • All axis labels, axis ranges, axis units, gridline values, and the
      numeric value at every labelled tick or annotation.
    • Every callout, label, arrow target, dimension, and depth marker exactly
      as written, with its associated value + unit.
    • Curves / tracks: name each curve, its colour or line style, its scale,
      and any notable inflections, peaks, crossovers, or shaded zones with the
      depth or time at which they occur.
    • Schematics / diagrams: list every component top-to-bottom (or
      inside-out) with its size, depth, MD/TVD, formation, weight, grade, and
      any tagged equipment IDs.
    • Maps: list named wells, fields, faults, contours (with values), grid
      coordinates, and any reference points.
    • Tables embedded as images: transcribe every cell verbatim.
    • Photos: describe subject, scale, condition, lithology, fluid, colour,
      and any visible measurement.
- Preserve units exactly as printed (ft, m, in, mm, psi, kPa, ppg, kg/m3, bbl,
  hrs, °F, °C, API, deg, %).
- Quote text shown on the image verbatim inside double quotes inside the
  description.
- If a value is unreadable, write `"<unreadable>"` rather than guessing.
- Length guidance: short generic photos may be 1–2 sentences; technical
  figures (logs, schematics, plots, maps) typically need 6–20 lines of
  detail. Do NOT pad with filler — only real, observable facts.
- Do NOT repeat content already covered by the page's `tables` or text
  `markdown`; focus on what is visually encoded in the image itself.

Self-assessment rules (CRITICAL):
- Set `self_confidence` based on completeness AND accuracy. If any region is
  unreadable, the page is degraded, or any table/diagram is dense/ambiguous,
  drop confidence accordingly.
- A more powerful model will RE-EXTRACT this page when `self_confidence < {pass_threshold}`
  OR `self_gaps` is non-empty. Use this — list every concrete weakness so the
  next model knows exactly where to focus.
- Do NOT inflate confidence to avoid escalation. Honesty maximizes final accuracy.

Return ONLY the JSON object, no extra text, no markdown code fences.{baseline_hint}"""


# ---------------------------------------------------------------------------
# Escalator (Gemini 3.1 Pro Preview — gap-aware, FINAL)
# ---------------------------------------------------------------------------

ESCALATION_SYSTEM = (
    "You are an expert document extraction agent using an advanced model. "
    "A faster model just extracted this page but flagged its own work as "
    "incomplete. You are given the page image, the previous extraction, "
    "the previous self-confidence score, and the specific gaps the previous "
    "model could not resolve. Your job: produce the FINAL, fully accurate "
    "extraction, paying special attention to the listed gaps.\n\n"
    "When extracting entities, you MUST use the controlled vocabulary below. "
    "Pick the closest matching value from the closed lists; do NOT invent "
    "new category strings.\n\n"
    + entity_type_hints()
)


def build_escalation_prompt(
    page_num: int,
    *,
    is_scanned: bool = False,
    baseline_text: str = "",
    previous_extraction_json: str = "",
    previous_confidence: float = 0.0,
    previous_gaps: str = "",
) -> str:
    """Build the prompt for the Gemini 3.1 Pro Preview escalation agent (FINAL stage)."""
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
            "\n\n--- PyMuPDF baseline text (independent extraction, hint only) ---\n"
            f"{snippet}\n"
            "--- END HINT ---"
        )

    previous_block = ""
    if previous_extraction_json:
        snippet = previous_extraction_json[:6000]
        if len(previous_extraction_json) > 6000:
            snippet += "\n... [truncated]"
        previous_block = (
            "\n\n--- Previous (Flash) extraction — fix and complete this ---\n"
            f"{snippet}\n"
            "--- END PREVIOUS ---"
        )

    gaps_block = (
        f"\n\n--- Gaps the previous model FLAGGED (focus here) ---\n{previous_gaps}\n--- END GAPS ---"
        if previous_gaps
        else ""
    )

    return f"""\
Re-extract page {page_num} from the attached image. The previous model self-reported \
confidence of {previous_confidence:.2f} and listed concrete gaps below.{scan_hint}{gaps_block}{previous_block}

Return the SAME JSON schema as the extractor (with bbox + entities):
{{
  "page_num": {page_num},
  "markdown": "<full page content as clean Markdown>",
  "tables":   [{{"id":"t1","markdown":"...","json_data":{{...}},"continues_on_next_page":false,"bbox":[ymin,xmin,ymax,xmax]}}],
  "images":   [{{"id":"img1","description":"<exhaustive per Image description rules>","location":"...","image_type":"...","bbox":[ymin,xmin,ymax,xmax]}}],
  "entities": [{{"type":"...","value":"...","unit":"...","context":"...","bbox":[ymin,xmin,ymax,xmax]}}],
  "cross_page_notes": "<if applicable>"
}}

Rules:
- This is the FINAL extraction. Resolve every flagged gap.
- Do NOT regress: keep correct content from the previous extraction, only fix
  what was missed or wrong.
- bbox arrays are [ymin, xmin, ymax, xmax] normalized 0-1000 (Gemini standard).
- Image filtering: ONLY include domain-meaningful images (well logs, schematics,
  diagrams, plots, maps, technical figures). SKIP logos, letterhead, watermarks,
  signature blocks, decorative borders, page ornaments, and any element a
  geologist or drilling engineer would never reference.
- Image descriptions MUST be exhaustive: capture title, axes (labels + units +
  ranges), every legend entry, every callout/annotation with its value + unit,
  every component in a schematic with its depth/size/grade, every curve with
  its scale and notable features, and any embedded tables transcribed verbatim.
  A reader who cannot see the image must be able to answer questions about it
  from the description alone. Use `\n` to separate facts; quote on-image text
  verbatim; write `<unreadable>` for illegible values.
- Return ONLY the JSON object, no extra text, no code fences.{baseline_hint}"""
