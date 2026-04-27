"""Document chunker — splits per-page markdown into overlapping chunks.

Uses ``RecursiveCharacterTextSplitter`` from langchain-text-splitters
(already a project dependency).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_text_splitters import RecursiveCharacterTextSplitter

if TYPE_CHECKING:
    from app.config import Settings


@dataclass
class Chunk:
    """A single text chunk with provenance metadata."""

    text: str
    doc_name: str
    job_id: str
    filename: str
    page_num: int
    chunk_index: int  # 0-based within a page


class DocumentChunker:
    """Split extracted page markdowns into overlapping chunks."""

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
        """Chunk all pages' markdown into a flat list of Chunks.

        Args:
            page_map: ``{page_num: extracted_dict}`` — each dict has a
                      ``markdown`` field.
            doc_name: Sanitized document name (for metadata).
            job_id: Ingestion job ID.
            filename: Original PDF filename.

        Returns:
            Ordered list of Chunks across all pages.
        """
        chunks: list[Chunk] = []

        for page_num in sorted(page_map.keys()):
            page_data = page_map[page_num]
            markdown = page_data.get("markdown", "")
            if not markdown or not markdown.strip():
                continue

            splits = self._splitter.split_text(markdown)
            for idx, text in enumerate(splits):
                chunks.append(
                    Chunk(
                        text=text,
                        doc_name=doc_name,
                        job_id=job_id,
                        filename=filename,
                        page_num=page_num,
                        chunk_index=idx,
                    )
                )

        return chunks
