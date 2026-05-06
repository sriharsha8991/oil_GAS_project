"""Phase 3: Post-Processing — assembles per-page results into final output.

Inputs:
  - baselines: Phase 0 PageBaseline list (used for header/footer detection
    and as fallback for pages with no extraction)
  - page_results: dict[page_num, PageState_dict] from the worker pool;
    each dict has .final = PageExtraction-shaped dict

Outputs (written to Supabase Storage via SupabaseStorage):
  1. per-page markdown:                pages/page_NNN.md
  2. per-page table JSON + markdown:   tables/page_NNN_table_MM.{json,md}
  3. merged cross-page table JSON:     tables/merged_table_NN.{json,md}
  4. combined document:                full_document.md
"""

from __future__ import annotations

import re

from app.domain.entity_resolver import CanonicalEntity, EntityResolver, ResolvedEntity
from app.models.schemas import PageBaseline
from app.storage.supabase_storage import SupabaseStorage
from app.utils.logging import get_logger

logger = get_logger(__name__)


class PostProcessor:
    """Transforms per-page extraction results into structured output files."""

    def __init__(
        self,
        storage: SupabaseStorage,
        user_id: str,
        project_id: str,
        document_id: str,
    ) -> None:
        self._storage = storage
        self._user_id = user_id
        self._project_id = project_id
        self._document_id = document_id

    async def run(
        self,
        doc_name: str,
        filename: str,
        baselines: list[PageBaseline],
        page_results: dict[int, dict],
    ) -> "PostProcessResult":
        """Execute the full post-processing pipeline."""
        # Step 1: Flatten page_results into a {page_num: extracted_dict} map
        page_map = _build_page_map(page_results)

        # Step 2: Merge cross-page tables (using continues_on_next_page flag)
        merged_tables = _merge_cross_page_tables(page_map)

        # Step 3: Detect repeated headers/footers
        header_footer = _detect_header_footer(page_map, baselines)

        # Step 4: Per-page markdown
        pages_written = 0
        for baseline in baselines:
            pn = baseline.page_num
            extracted = page_map.get(pn)

            md = (
                _format_extracted_page(pn, extracted, header_footer)
                if extracted
                else _format_baseline_page(baseline)
            )

            await self._storage.write_page_markdown(
                self._user_id, self._project_id, self._document_id, pn, md
            )
            pages_written += 1

        # Step 5: Per-page table files
        tables_written = 0
        for pn, page_data in sorted(page_map.items()):
            for idx, table in enumerate(page_data.get("tables", [])):
                json_data = table.get("json_data", {})
                if not json_data:
                    json_data = _table_md_to_json(table.get("markdown", ""))
                await self._storage.write_table_json(
                    self._user_id, self._project_id, self._document_id,
                    pn, idx + 1, json_data
                )

                table_md = table.get("markdown", "")
                if table_md:
                    await self._storage.write_table_markdown(
                        self._user_id, self._project_id, self._document_id,
                        pn, idx + 1, table_md
                    )
                tables_written += 1

        # Step 6: Merged cross-page tables
        for mt in merged_tables:
            await self._storage.write_table_json(
                self._user_id,
                self._project_id,
                self._document_id,
                mt["page_start"],
                mt["table_index"],
                {
                    "merged": True,
                    "page_range": [mt["page_start"], mt["page_end"]],
                    "data": mt["json_data"],
                },
            )
            if mt.get("markdown"):
                await self._storage.write_table_markdown(
                    self._user_id,
                    self._project_id,
                    self._document_id,
                    mt["page_start"],
                    mt["table_index"],
                    mt["markdown"],
                )
            tables_written += 1

        # Step 7: Combined full document
        full_md = _build_full_document(filename, baselines, page_map, header_footer)
        await self._storage.write_full_document(
            self._user_id, self._project_id, self._document_id, full_md
        )

        # Step 8: Entity resolution (L2 domain knowledge)
        resolver = EntityResolver()
        canonical_store: list[CanonicalEntity] = []
        resolved_by_page: dict[int, list[dict]] = {}

        for pn in sorted(page_map.keys()):
            raw_entities = page_map[pn].get("entities", []) or []
            if not raw_entities:
                continue
            resolved, canonical_store = resolver.resolve_entities(
                raw_entities,
                canonical_store,
                doc_name=doc_name,
                page_num=pn,
            )
            resolved_by_page[pn] = [
                {
                    "type": r.type,
                    "value": r.value,
                    "unit": r.unit,
                    "context": r.context,
                    "bbox": r.bbox,
                    "canonical_id": r.canonical_id,
                    "canonical_value": r.canonical_value,
                    "resolution": r.resolution,
                    "similarity": round(r.similarity, 3),
                }
                for r in resolved
            ]

        # Step 9: Consolidated entities (one file per doc, organized by page)
        entities_doc = _build_entities_doc(doc_name, page_map)
        # Enrich with resolution data
        entities_doc["resolved_by_page"] = {
            str(k): v for k, v in resolved_by_page.items()
        }
        entities_doc["canonical_entities"] = [
            {
                "canonical_id": c.canonical_id,
                "entity_type": c.entity_type,
                "canonical_value": c.canonical_value,
                "unit": c.unit,
                "aliases": c.aliases,
                "occurrences": c.occurrences,
            }
            for c in canonical_store
        ]
        entities_count = sum(len(v) for v in entities_doc["entities_by_page"].values())
        resolved_count = sum(len(v) for v in resolved_by_page.values())
        await self._storage.write_entities_json(
            self._user_id, self._project_id, self._document_id, entities_doc
        )

        result = PostProcessResult(
            pages_written=pages_written,
            tables_written=tables_written,
            merged_tables=len(merged_tables),
            header_footer_removed=len(header_footer),
            entities_written=entities_count,
            entities_resolved=resolved_count,
            canonical_entities=len(canonical_store),
            resolved_by_page=resolved_by_page,
        )

        logger.info(
            "postprocessor_done",
            doc_name=doc_name,
            pages=pages_written,
            tables=tables_written,
            merged_tables=len(merged_tables),
            entities=entities_count,
            entities_resolved=resolved_count,
            canonical_entities=len(canonical_store),
        )
        return result


class PostProcessResult:
    """Stats from post-processing."""

    def __init__(
        self,
        pages_written: int = 0,
        tables_written: int = 0,
        merged_tables: int = 0,
        header_footer_removed: int = 0,
        entities_written: int = 0,
        entities_resolved: int = 0,
        canonical_entities: int = 0,
        resolved_by_page: dict[int, list[dict]] | None = None,
    ) -> None:
        self.pages_written = pages_written
        self.tables_written = tables_written
        self.merged_tables = merged_tables
        self.header_footer_removed = header_footer_removed
        self.entities_written = entities_written
        self.entities_resolved = entities_resolved
        self.canonical_entities = canonical_entities
        self.resolved_by_page = resolved_by_page or {}

    def to_dict(self) -> dict[str, int]:
        return {
            "pages_written": self.pages_written,
            "tables_written": self.tables_written,
            "merged_tables": self.merged_tables,
            "header_footer_removed": self.header_footer_removed,
            "entities_written": self.entities_written,
            "entities_resolved": self.entities_resolved,
            "canonical_entities": self.canonical_entities,
        }


# ---------------------------------------------------------------------------
# Page map
# ---------------------------------------------------------------------------


def _build_page_map(page_results: dict[int, dict]) -> dict[int, dict]:
    """Flatten {page_num: PageState_dict} into {page_num: PageExtraction_dict}.

    Each PageState dict has a `.final` field with the locked-in extraction.
    Falls back to `.extracted` if `.final` is missing.
    """
    page_map: dict[int, dict] = {}
    for page_num, state in page_results.items():
        if not isinstance(state, dict):
            continue
        extracted = state.get("final") or state.get("extracted") or {}
        if extracted:
            page_map[int(page_num)] = extracted
    return page_map


# ---------------------------------------------------------------------------
# Cross-page table merging (uses extractor-set continues_on_next_page flag)
# ---------------------------------------------------------------------------


def _merge_cross_page_tables(page_map: dict[int, dict]) -> list[dict]:
    """Stitch tables flagged with `continues_on_next_page=true`.

    Walks pages in order. When a page's last table flags continuation AND
    the next page has at least one table with the same column count,
    appends the next page's first table as a continuation. Continues
    through any chain of consecutive flagged pages.
    """
    if not page_map:
        return []

    sorted_pages = sorted(page_map.keys())
    merged: list[dict] = []
    consumed: set[tuple[int, int]] = set()  # (page_num, table_index)
    next_index = 1

    for i, pn in enumerate(sorted_pages):
        page = page_map[pn]
        tables = page.get("tables", [])
        if not tables:
            continue

        last_idx = len(tables) - 1
        last_table = tables[last_idx]
        if not last_table.get("continues_on_next_page"):
            continue
        if (pn, last_idx) in consumed:
            continue

        chain_md: list[str] = [last_table.get("markdown", "")]
        chain_json: list[dict] = []
        first_json = last_table.get("json_data") or {}
        if first_json:
            chain_json.append(first_json)
        page_end = pn
        consumed.add((pn, last_idx))

        # Walk forward through consecutive pages
        ref_col_count = _column_count(last_table)
        cursor = i + 1
        while cursor < len(sorted_pages):
            next_pn = sorted_pages[cursor]
            # Only continue if pages are truly adjacent
            if next_pn != page_end + 1:
                break
            next_tables = page_map[next_pn].get("tables", [])
            if not next_tables:
                break
            cont_table = next_tables[0]
            if _column_count(cont_table) != ref_col_count and ref_col_count > 0:
                break

            chain_md.append(cont_table.get("markdown", ""))
            cont_json = cont_table.get("json_data") or {}
            if cont_json:
                chain_json.append(cont_json)
            consumed.add((next_pn, 0))
            page_end = next_pn

            if not cont_table.get("continues_on_next_page"):
                break
            cursor += 1

        if page_end == pn:
            continue  # nothing actually merged

        merged.append({
            "page_start": pn,
            "page_end": page_end,
            "table_index": next_index,
            "markdown": _merge_table_markdown([m for m in chain_md if m]),
            "json_data": _merge_table_json(chain_json),
        })
        next_index += 1

    return merged


def _column_count(table: dict) -> int:
    jd = table.get("json_data") or {}
    headers = jd.get("headers") or []
    if headers:
        return len(headers)
    md = table.get("markdown", "")
    for line in md.splitlines():
        line = line.strip()
        if line.startswith("|") and not re.match(r"^\|?[\s\-:|]+$", line):
            cells = [c for c in line.split("|") if c.strip()]
            return len(cells)
    return 0


def _merge_table_markdown(fragments: list[str]) -> str:
    """Combine multiple table fragments — keep first header, append rows."""
    if not fragments:
        return ""
    if len(fragments) == 1:
        return fragments[0]

    result_lines = list(fragments[0].strip().split("\n"))

    for frag in fragments[1:]:
        frag_lines = frag.strip().split("\n")
        sep_idx = -1
        for i, line in enumerate(frag_lines):
            if re.match(r"^\|?[\s\-:]+\|", line):
                sep_idx = i
                break
        data_start = sep_idx + 1 if sep_idx >= 0 else 0
        for j in range(data_start, len(frag_lines)):
            line = frag_lines[j].strip()
            if line and not re.match(
                r"^(table\s+\d+\.?\s*(continued|cont[.'d])|---|$)", line, re.I
            ):
                result_lines.append(frag_lines[j])

    return "\n".join(result_lines)


def _merge_table_json(fragments: list[dict]) -> dict:
    """Combine JSON table fragments — accumulate rows."""
    if not fragments:
        return {}
    if len(fragments) == 1:
        return fragments[0]

    merged = dict(fragments[0])
    rows = list(merged.get("rows", []))
    for frag in fragments[1:]:
        rows.extend(frag.get("rows", []))
    merged["rows"] = rows
    return merged


# ---------------------------------------------------------------------------
# Header/footer detection
# ---------------------------------------------------------------------------


def _detect_header_footer(
    page_map: dict[int, dict],
    baselines: list[PageBaseline],
) -> set[str]:
    """Detect lines that appear on most pages (likely headers/footers).

    Tiered threshold tuning:
    - Documents with <10 pages: skip detection entirely (false-positive risk
      outweighs the cleanup, e.g. a section heading repeated across the only
      few pages of that section gets mis-stripped).
    - 10-29 pages: require ~70% co-occurrence.
    - 30+ pages: require ~50% co-occurrence (original behaviour).
    Candidate lines must also be reasonably short (<=80 chars) to avoid
    matching legitimate paragraphs.
    """
    if len(baselines) < 10:
        return set()

    line_counts: dict[str, int] = {}
    total_pages = 0

    for baseline in baselines:
        text = baseline.text.strip()
        if not text:
            continue
        total_pages += 1
        non_empty = [l.strip() for l in text.split("\n") if l.strip()]
        candidates = set()
        for line in non_empty[:2] + non_empty[-2:]:
            cleaned = _normalize_header_line(line)
            if cleaned and 5 < len(cleaned) <= 80:
                candidates.add(cleaned)
        for c in candidates:
            line_counts[c] = line_counts.get(c, 0) + 1

    ratio = 0.7 if total_pages < 30 else 0.5
    threshold = max(total_pages * ratio, 4)
    return {line for line, count in line_counts.items() if count >= threshold}


def _normalize_header_line(line: str) -> str:
    cleaned = re.sub(r"[_*#`]", "", line).strip()
    cleaned = re.sub(r"^\d+$", "", cleaned)
    cleaned = re.sub(r"\b[ivxlcdm]+\b$", "", cleaned, flags=re.I)
    return cleaned.strip()


def _remove_header_footer(text: str, header_footer: set[str]) -> str:
    if not header_footer:
        return text
    out = []
    for line in text.split("\n"):
        if _normalize_header_line(line) in header_footer:
            continue
        out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Page formatting
# ---------------------------------------------------------------------------


def _format_extracted_page(
    page_num: int,
    extracted: dict,
    header_footer: set[str],
) -> str:
    """Format an extracted page as markdown."""
    parts: list[str] = []

    md = extracted.get("markdown", "")
    md = _remove_header_footer(md, header_footer)
    parts.append(md)

    images = extracted.get("images", [])
    if images:
        parts.append("")
        for img in images:
            desc = img.get("description", "")
            img_id = img.get("id", "")
            location = img.get("location", "")
            if desc:
                label = f"**[{img_id}]** " if img_id else ""
                loc = f" *({location})*" if location else ""
                parts.append(f"> \U0001f5bc {label}{desc}{loc}")

    notes = extracted.get("cross_page_notes", "")
    if notes:
        parts.append("")
        parts.append(f"> *{notes}*")

    parts.append("")
    return "\n".join(parts)


def _format_baseline_page(baseline: PageBaseline) -> str:
    """Format a baseline page (fallback when extraction is missing)."""
    lines = [
        f"# Page {baseline.page_num + 1}",
        "",
        f"**Extraction method**: {baseline.extraction_method}  ",
        f"**Characters**: {baseline.char_count}  ",
        "",
        "---",
        "",
    ]
    if baseline.text:
        lines.append(baseline.text)
    else:
        lines.append("*(No text extracted — page appears to be image-only)*")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Full document builder
# ---------------------------------------------------------------------------


def _build_full_document(
    filename: str,
    baselines: list[PageBaseline],
    page_map: dict[int, dict],
    header_footer: set[str],
) -> str:
    """Build the combined full_document.md by concatenating per-page markdown.

    The first H1 in page 0's markdown is promoted to the document title.
    """
    parts: list[str] = []

    title = _derive_title(filename, page_map)
    parts.append(f"# {title}")
    parts.append("")
    parts.append(f"**Total pages**: {len(baselines)}  ")
    parts.append("")
    parts.append("---")
    parts.append("")

    for baseline in baselines:
        pn = baseline.page_num
        extracted = page_map.get(pn)

        parts.append(f"<!-- Page {pn + 1} -->")

        if extracted:
            md = _remove_header_footer(extracted.get("markdown", ""), header_footer)
            parts.append(md)

            for img in extracted.get("images", []):
                desc = img.get("description", "")
                img_id = img.get("id", "")
                location = img.get("location", "")
                if desc:
                    label = f"**[{img_id}]** " if img_id else ""
                    loc = f" *({location})*" if location else ""
                    parts.append(f"> \U0001f5bc {label}{desc}{loc}")

            notes = extracted.get("cross_page_notes", "")
            if notes:
                parts.append(f"> *{notes}*")
        else:
            parts.append(baseline.text if baseline.text else "*(No text extracted)*")

        parts.append("")
        parts.append("---")
        parts.append("")

    return "\n".join(parts)


def _derive_title(filename: str, page_map: dict[int, dict]) -> str:
    """Pull the first H1 from page 0; fall back to the filename stem."""
    page0 = page_map.get(0)
    if page0:
        for line in page0.get("markdown", "").splitlines():
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("## "):
                return stripped[2:].strip()
    return filename


# ---------------------------------------------------------------------------
# Table utilities
# ---------------------------------------------------------------------------


def _table_md_to_json(table_md: str) -> dict:
    """Best-effort conversion of a markdown table to JSON."""
    if not table_md.strip():
        return {}

    lines = [l.strip() for l in table_md.strip().split("\n") if l.strip()]
    if not lines:
        return {}

    header_line = None
    data_start = 0
    for i, line in enumerate(lines):
        if re.match(r"^\|?[\s\-:]+\|", line):
            if i > 0:
                header_line = lines[i - 1]
            data_start = i + 1
            break

    if header_line is None and lines:
        header_line = lines[0]
        data_start = 1

    def _parse_row(line: str) -> list[str]:
        cells = line.split("|")
        if cells and not cells[0].strip():
            cells = cells[1:]
        if cells and not cells[-1].strip():
            cells = cells[:-1]
        return [c.strip() for c in cells]

    headers = _parse_row(header_line) if header_line else []
    rows = []
    for line in lines[data_start:]:
        if re.match(r"^\|?[\s\-:]+\|", line):
            continue
        rows.append(_parse_row(line))

    return {"headers": headers, "rows": rows}


# ---------------------------------------------------------------------------
# Entities consolidation
# ---------------------------------------------------------------------------


def _build_entities_doc(doc_name: str, page_map: dict[int, dict]) -> dict:
    """Build a per-doc entities artifact: {entities_by_page, by_type counts}."""
    entities_by_page: dict[str, list[dict]] = {}
    by_type: dict[str, int] = {}

    for pn in sorted(page_map.keys()):
        items = page_map[pn].get("entities", []) or []
        if not items:
            continue
        entities_by_page[str(pn)] = items
        for ent in items:
            t = (ent.get("type") or "other").strip() or "other"
            by_type[t] = by_type.get(t, 0) + 1

    return {
        "doc_name": doc_name,
        "by_type": by_type,
        "entities_by_page": entities_by_page,
    }

