"""Document chunker — splits per-page content into retrieval-ready chunks.

Two chunk kinds:
  - "narrative":  prose text from a page's markdown, after tables are stripped.
                  Uses RecursiveCharacterTextSplitter with section-aware separators.
  - "table":      one chunk per extracted table (full table preserved). Tables are
                  NEVER sliced by the recursive splitter.

This keeps tables intact for retrieval (a "casing program" query returns the
whole table) and prevents narrative chunks from being polluted with table rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

if TYPE_CHECKING:
    from app.config import Settings


_MIN_NARRATIVE_CHARS = 100  # discard sub-100-char fragments


@dataclass
class Chunk:
    """A single retrievable chunk with provenance + structure metadata."""

    text: str
    doc_name: str
    job_id: str
    filename: str
    page_num: int
    chunk_index: int          # 0-based within a page
    kind: str = "narrative"   # "narrative" | "table"
    table_id: str = ""
    table_markdown: str = ""  # populated for kind == "table"
    bbox: list[int] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# Match a contiguous Markdown table block (header + separator + body rows).
# Captures groups: 1=full block.
_TABLE_BLOCK_RE = re.compile(
    r"(?:^|\n)((?:\|[^\n]*\|\s*\n)(?:\|[\s\-:|]+\|\s*\n)(?:\|[^\n]*\|\s*\n?)+)",
    re.MULTILINE,
)


def _strip_inline_tables(markdown: str) -> str:
    """Replace every Markdown table block with a placeholder line.

    Keeps narrative text contiguous and prevents the recursive splitter
    from slicing a table mid-row.
    """
    if not markdown:
        return markdown
    counter = {"n": 0}

    def _sub(_m: re.Match[str]) -> str:
        counter["n"] += 1
        return f"\n[Table T{counter['n']} — see table chunk]\n"

    return _TABLE_BLOCK_RE.sub(_sub, markdown)


class DocumentChunker:
    """Split extracted page content into narrative + table chunks."""

    def __init__(self, settings: "Settings") -> None:
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            length_function=len,
            separators=["\n## ", "\n### ", "\n\n", "\n", ". ", " ", ""],
        )

    def chunk_pages(
        self,
        page_map: dict[int, dict],
        *,
        doc_name: str,
        job_id: str,
        filename: str,
    ) -> list[Chunk]:
        """Build narrative + table chunks for every page."""
        chunks: list[Chunk] = []

        for page_num in sorted(page_map.keys()):
            page = page_map[page_num] or {}
            page_chunks = self._chunk_one_page(
                page,
                page_num=page_num,
                doc_name=doc_name,
                job_id=job_id,
                filename=filename,
            )
            chunks.extend(page_chunks)

        return chunks

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _chunk_one_page(
        self,
        page: dict,
        *,
        page_num: int,
        doc_name: str,
        job_id: str,
        filename: str,
    ) -> list[Chunk]:
        out: list[Chunk] = []
        idx = 0

        # 1) Table chunks (one per extracted table; full markdown preserved).
        for t in page.get("tables", []) or []:
            table_md = (t.get("markdown") or "").strip()
            if not table_md:
                continue
            preamble = self._table_preamble(t, page_num)
            text = f"{preamble}\n\n{table_md}"
            out.append(
                Chunk(
                    text=text,
                    doc_name=doc_name,
                    job_id=job_id,
                    filename=filename,
                    page_num=page_num,
                    chunk_index=idx,
                    kind="table",
                    table_id=str(t.get("id") or f"t{idx + 1}"),
                    table_markdown=table_md,
                    bbox=t.get("bbox"),
                    extra={
                        "continues_on_next_page": bool(
                            t.get("continues_on_next_page")
                        ),
                    },
                )
            )
            idx += 1

        # 2) Narrative chunks from markdown with tables stripped out.
        markdown = page.get("markdown", "") or ""
        if markdown.strip():
            narrative = _strip_inline_tables(markdown)
            for split in self._splitter.split_text(narrative):
                clean = split.strip()
                if len(clean) < _MIN_NARRATIVE_CHARS:
                    continue
                out.append(
                    Chunk(
                        text=clean,
                        doc_name=doc_name,
                        job_id=job_id,
                        filename=filename,
                        page_num=page_num,
                        chunk_index=idx,
                        kind="narrative",
                    )
                )
                idx += 1

        return out

    @staticmethod
    def _table_preamble(t: dict, page_num: int) -> str:
        """Synthetic context line so the embedding captures table identity."""
        json_data = t.get("json_data") or {}
        headers = json_data.get("headers") or []
        rows = json_data.get("rows") or []
        bits = [f"Table on page {page_num + 1}"]
        if headers:
            bits.append(f"Headers: {', '.join(str(h) for h in headers)}")
        if rows:
            bits.append(f"{len(rows)} rows")
        if t.get("continues_on_next_page"):
            bits.append("continues on next page")
        return ". ".join(bits) + "."
