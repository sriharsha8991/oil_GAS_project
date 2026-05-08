"""Single-query fan-out RAG search across the 3 Qdrant collections.

Used by the synthesizer's ``rag_search`` tool. The synthesizer (an LLM
with function-calling) decides if/when/how to call this — including
whether to decompose into multiple sub-queries (max 3 calls per turn).

Images are pulled only when the caller asks for them; the synthesizer
sets ``include_images=True`` only when the user explicitly mentioned a
visual ("image", "figure", "diagram", "chart", "show me", etc.).

Each search result is followed by a +/- 1 chunk_index neighbor expansion
on the top narrative chunks to recover info that crosses chunk boundaries
(definitions split across chunks, multi-paragraph procedures, etc.).
"""

from __future__ import annotations

import asyncio
import re

from app.agents.rag_tools import (
    SearchResult,
    search_chunks,
    search_entities,
    search_images,
)
from app.utils.logging import get_logger
from app.vectorstore.embedder import GeminiEmbedder
from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)

# Number of top narrative chunks to expand neighbors for, per call.
NEIGHBOR_EXPAND_TOP_K = 3


_IMAGE_TRIGGER_RE = re.compile(
    r"\b(image|images|figure|figures|diagram|diagrams|chart|charts|plot|plots|"
    r"picture|pictures|photo|photos|map|maps|graph|graphs|visual|visuals|"
    r"illustrat|sketch|screenshot|render|show me|see the)\b",
    re.IGNORECASE,
)


def wants_images(message: str) -> bool:
    """True iff the user message explicitly asks for visual content."""
    return bool(_IMAGE_TRIGGER_RE.search(message or ""))


async def rag_search(
    query: str,
    *,
    embedder: GeminiEmbedder,
    vector_store: QdrantVectorStore,
    user_id: str,
    project_id: str | None = None,
    document_id: str | None = None,
    include_images: bool = False,
    chunk_limit: int = 8,
    entity_limit: int = 6,
    image_limit: int = 4,
    score_threshold: float = 0.3,
) -> list[SearchResult]:
    """Run chunks + entities (and optionally images) in parallel.

    Returns a flat list deduped across collections and sorted by score.
    """
    if not query or not query.strip():
        return []

    coros: list = [
        search_chunks(
            query, embedder=embedder, vector_store=vector_store,
            user_id=user_id, limit=chunk_limit,
            project_id=project_id, document_id=document_id,
            score_threshold=score_threshold,
        ),
        search_entities(
            query, embedder=embedder, vector_store=vector_store,
            user_id=user_id, limit=entity_limit,
            project_id=project_id, document_id=document_id,
            score_threshold=score_threshold,
        ),
    ]
    if include_images:
        coros.append(search_images(
            query, embedder=embedder, vector_store=vector_store,
            user_id=user_id, limit=image_limit,
            project_id=project_id, document_id=document_id,
            score_threshold=score_threshold,
        ))

    results = await asyncio.gather(*coros, return_exceptions=True)

    seen: set[tuple] = set()
    out: list[SearchResult] = []
    for part in results:
        if isinstance(part, Exception):
            logger.warning("rag_search_part_failed", error=str(part))
            continue
        for h in part:
            key = (
                h.collection, h.doc_name, h.page_num, h.chunk_index,
                h.table_id, h.canonical_id,
                tuple(h.bbox) if h.bbox else None,
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(h)

    out.sort(key=lambda h: h.score, reverse=True)
    # Same-document +/-1 chunk_index neighbor expansion on the top narrative
    # chunks. Cheap (one bounded scroll) and frequently turns "near miss"
    # answers into complete ones when the relevant sentence sat across a
    # chunk boundary.
    await _expand_neighbors(
        out,
        vector_store=vector_store,
        user_id=user_id,
        project_id=project_id,
        document_id=document_id,
    )

    logger.info(
        "rag_search_done", query=query[:80], hits=len(out),
        include_images=include_images,
    )
    return out


# ---------------------------------------------------------------------------
# Neighbor expansion (+/- 1 chunk_index within same doc + page)
# ---------------------------------------------------------------------------

async def _expand_neighbors(
    results: list[SearchResult],
    *,
    vector_store: QdrantVectorStore,
    user_id: str,
    project_id: str | None,
    document_id: str | None,
) -> None:
    """Append +/- 1 chunk_index neighbors of the top narrative chunks (in place).

    - Only narrative chunks are expanded (table chunks are self-contained).
    - Neighbors share (doc_name, page_num); we don't cross page boundaries
      because the chunker resets chunk_index per page.
    - Neighbor score is set just below the seed chunk so it never displaces
      a real high-score hit in the synthesizer's view.
    - Already-present (doc_name, page_num, chunk_index) keys are skipped.
    """
    seeds: list[SearchResult] = []
    for r in results:
        if r.collection != "wellsynthai_documents":
            continue
        if r.kind == "table":
            continue
        if r.doc_name is None or r.page_num is None or r.chunk_index is None:
            continue
        seeds.append(r)
        if len(seeds) >= NEIGHBOR_EXPAND_TOP_K:
            break
    if not seeds:
        return

    have: set[tuple] = {
        (r.doc_name, r.page_num, r.chunk_index)
        for r in results
        if r.doc_name is not None
        and r.page_num is not None
        and r.chunk_index is not None
    }
    keys: list[tuple[str, int, int]] = []
    for s in seeds:
        for delta in (-1, 1):
            ci = (s.chunk_index or 0) + delta
            if ci < 0:
                continue
            key = (s.doc_name, s.page_num, ci)
            if key in have:
                continue
            have.add(key)
            keys.append(key)  # type: ignore[arg-type]

    if not keys:
        return

    try:
        rows = await vector_store.fetch_chunks_by_keys(
            keys,
            user_id=user_id,
            project_id=project_id,
            document_id=document_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("neighbor_expand_failed", error=str(exc))
        return

    # Score each neighbor just below its seed so ordering stays stable.
    seed_score = {
        (s.doc_name, s.page_num): s.score for s in seeds
    }
    for row in rows:
        seed_key = (row.get("doc_name"), row.get("page_num"))
        score = max(0.0, seed_score.get(seed_key, 0.3) - 0.05)
        results.append(SearchResult(
            text=row.get("text", ""),
            score=score,
            user_id=row.get("user_id"),
            project_id=row.get("project_id"),
            document_id=row.get("document_id"),
            doc_name=row.get("doc_name"),
            filename=row.get("filename"),
            job_id=row.get("job_id"),
            page_num=row.get("page_num"),
            chunk_index=row.get("chunk_index"),
            kind=row.get("kind"),
            table_id=row.get("table_id"),
            table_markdown=row.get("table_markdown"),
            bbox=row.get("bbox"),
            collection="wellsynthai_documents",
        ))
    # Re-sort so the model sees descending scores after the merge.
    results.sort(key=lambda h: h.score, reverse=True)
    logger.info("neighbor_expanded", added=len(rows), seeds=len(seeds))
