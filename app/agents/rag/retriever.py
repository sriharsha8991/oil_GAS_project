"""Retrieval — fan out a QueryPlan across the 3 search tools, assess coverage,
and run the recovery ladder when results are weak.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from app.agents.rag.types import (
    Bundle,
    QueryPlan,
    RetrievalAssessment,
)
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

# Coverage / sufficiency thresholds
_TOP_SCORE_OK = 0.45
_TOP_SCORE_FLOOR = 0.40    # below this with no facet coverage → no_evidence


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------

async def retrieve(
    plan: QueryPlan,
    *,
    embedder: GeminiEmbedder,
    vector_store: QdrantVectorStore,
    user_id: str,
    project_id: str | None = None,
    document_id: str | None = None,
    score_threshold: float = 0.3,
    chunk_limit: int = 10,
    entity_limit: int = 12,
    image_limit: int = 5,
) -> Bundle:
    """Run all queries in the plan in parallel and return a merged Bundle."""
    # Tabular intent → force table-kind chunks and pull more entities.
    # Casing programs / BHA lists / depth tables live in the table chunks
    # and the entity collection; making this deterministic removes a class
    # of misses where the planner forgot to set chunk_kinds.
    if plan.intent == "tabular":
        if not plan.chunk_kinds:
            plan.chunk_kinds = ["table"]
        entity_limit = max(entity_limit, 20)

    coros: list[asyncio.Task] = []
    tags: list[str] = []

    for q in plan.chunk_queries:
        coros.append(asyncio.create_task(search_chunks(
            q,
            embedder=embedder,
            vector_store=vector_store,
            user_id=user_id,
            limit=chunk_limit,
            project_id=project_id,
            document_id=document_id,
            score_threshold=score_threshold,
            kinds=plan.chunk_kinds,
        )))
        tags.append("chunks")

    for eq in plan.entity_queries:
        coros.append(asyncio.create_task(search_entities(
            eq.q,
            embedder=embedder,
            vector_store=vector_store,
            user_id=user_id,
            limit=entity_limit,
            project_id=project_id,
            document_id=document_id,
            entity_type=eq.entity_type,
            unit=eq.unit,
            score_threshold=score_threshold,
        )))
        tags.append("entities")

    if plan.use_images:
        for q in plan.image_queries:
            coros.append(asyncio.create_task(search_images(
                q,
                embedder=embedder,
                vector_store=vector_store,
                user_id=user_id,
                limit=image_limit,
                project_id=project_id,
                document_id=document_id,
                score_threshold=score_threshold,
                image_types=plan.image_types,
            )))
            tags.append("images")

    if not coros:
        return Bundle()

    results = await asyncio.gather(*coros, return_exceptions=True)

    bundle = Bundle()
    seen: set[tuple] = set()

    def _add(target: list[SearchResult], hits: list[SearchResult]) -> None:
        for h in hits:
            k = (h.collection, h.doc_name, h.page_num, h.chunk_index,
                 h.table_id, h.canonical_id, tuple(h.bbox) if h.bbox else None)
            if k in seen:
                continue
            seen.add(k)
            target.append(h)

    for tag, res in zip(tags, results):
        if isinstance(res, Exception):
            logger.warning("retrieve_tool_failed", tool=tag, error=str(res))
            continue
        if tag == "chunks":
            _add(bundle.chunks, res)
        elif tag == "entities":
            _add(bundle.entities, res)
        elif tag == "images":
            _add(bundle.images, res)

    # Re-sort each list by score (highest first).
    bundle.chunks.sort(key=lambda r: r.score, reverse=True)
    bundle.entities.sort(key=lambda r: r.score, reverse=True)
    bundle.images.sort(key=lambda r: r.score, reverse=True)

    logger.info(
        "retrieve_done",
        chunks=len(bundle.chunks),
        entities=len(bundle.entities),
        images=len(bundle.images),
    )
    return bundle


# ---------------------------------------------------------------------------
# Assessment — does the bundle cover what the plan asked for?
# ---------------------------------------------------------------------------

def _facet_tokens(facet: str) -> list[str]:
    """Crude tokenization — split on _ and drop short stopwords."""
    parts = [p.lower() for p in facet.replace("-", "_").split("_") if len(p) > 2]
    return parts


def _hit_text(r: SearchResult) -> str:
    bits: list[str] = [r.text or ""]
    if r.canonical_value:
        bits.append(r.canonical_value)
    if r.entity_type:
        bits.append(r.entity_type)
    if r.image_type:
        bits.append(r.image_type)
    if r.description:
        bits.append(r.description)
    if r.table_markdown:
        bits.append(r.table_markdown[:500])
    return " ".join(bits).lower()


def assess(
    plan: QueryPlan,
    bundle: Bundle,
    *,
    top_score_threshold: float = _TOP_SCORE_OK,
) -> RetrievalAssessment:
    """Compute facet coverage + a sufficiency verdict."""
    hits = bundle.all_hits()
    if not hits:
        return RetrievalAssessment(
            sufficient=False,
            top_score=0.0,
            covered_facets=[],
            missing_facets=list(plan.required_facets),
            reason="no_hits",
        )

    top_score = max(h.score for h in hits)

    if not plan.required_facets:
        # No facet contract — fall back to score-only.
        sufficient = top_score >= top_score_threshold
        return RetrievalAssessment(
            sufficient=sufficient,
            top_score=top_score,
            covered_facets=[],
            missing_facets=[],
            reason="ok" if sufficient else "low_score",
        )

    # Facet coverage: a facet is covered if any hit's text contains
    # at least one meaningful token from the facet name.
    blob = " ".join(_hit_text(h) for h in hits[:30])  # cap for speed
    covered: list[str] = []
    missing: list[str] = []
    for facet in plan.required_facets:
        toks = _facet_tokens(facet)
        if not toks:
            covered.append(facet)
            continue
        if any(t in blob for t in toks):
            covered.append(facet)
        else:
            missing.append(facet)

    sufficient = (
        not missing
        and top_score >= top_score_threshold
    )
    if not sufficient:
        if missing and top_score >= top_score_threshold:
            reason = "missing_facets"
        elif top_score < top_score_threshold and not missing:
            reason = "low_score"
        else:
            reason = "missing_facets"
    else:
        reason = "ok"

    return RetrievalAssessment(
        sufficient=sufficient,
        top_score=top_score,
        covered_facets=covered,
        missing_facets=missing,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Recovery ladder
# ---------------------------------------------------------------------------

async def broaden_retry(
    plan: QueryPlan,
    *,
    embedder: GeminiEmbedder,
    vector_store: QdrantVectorStore,
    user_id: str,
    project_id: str | None = None,
    document_id: str | None = None,
) -> Bundle:
    """Rung 1 — drop kind/entity_type filters, lower threshold, raise limits."""
    broadened = QueryPlan(
        intent=plan.intent,
        required_facets=plan.required_facets,
        chunk_queries=plan.chunk_queries or [],
        chunk_kinds=None,
        entity_queries=[
            type(eq)(q=eq.q, entity_type=None, unit=None)
            for eq in plan.entity_queries
        ],
        image_queries=plan.image_queries,
        image_types=None,
        use_images=plan.use_images,
        raw={"broadened": True},
    )
    return await retrieve(
        broadened,
        embedder=embedder,
        vector_store=vector_store,
        user_id=user_id,
        project_id=project_id,
        document_id=document_id,
        score_threshold=0.15,
        chunk_limit=20,
        entity_limit=20,
        image_limit=8,
    )


def is_hard_floor(assessment: RetrievalAssessment) -> bool:
    """Return True when retrieval is so weak we should refuse to answer."""
    return (
        assessment.top_score < _TOP_SCORE_FLOOR
        and not assessment.covered_facets
    )


def truncate_for_synth(
    bundle: Bundle,
    *,
    chunk_top_k: int = 6,
    entity_top_k: int = 12,
    image_top_k: int = 4,
) -> Bundle:
    """Trim to top-K per collection before sending to the synthesizer."""
    return Bundle(
        chunks=bundle.chunks[:chunk_top_k],
        entities=bundle.entities[:entity_top_k],
        images=bundle.images[:image_top_k],
    )


# ---------------------------------------------------------------------------
# MMR — diversify chunks before truncation
# ---------------------------------------------------------------------------

_TOK_RE = re.compile(r"[a-z0-9]+")


def _toks(text: str) -> set[str]:
    if not text:
        return set()
    return {t for t in _TOK_RE.findall(text.lower()) if len(t) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


def mmr_diversify_chunks(
    bundle: Bundle,
    *,
    k: int = 8,
    lambda_: float = 0.7,
) -> None:
    """Greedy lexical MMR over ``bundle.chunks`` (in place).

    Cuts near-duplicate paragraphs that bloat the synthesizer prompt
    without adding information. Operates on token Jaccard similarity so
    we don't need to re-embed; cheap and effective for chunk-level dedup.

    Mutates ``bundle.chunks`` in place: replaces the list with a
    re-ordered, MMR-selected subset (size ≤ k). Original score is kept.
    """
    pool = list(bundle.chunks)
    if len(pool) <= 1:
        return

    # Pre-tokenize once.
    tokens = [_toks(c.text or "") for c in pool]
    rel = [c.score for c in pool]  # relevance = retrieval score

    selected: list[int] = []
    # Seed with the top-scoring item.
    selected.append(0)
    remaining = list(range(1, len(pool)))

    while remaining and len(selected) < k:
        best_i = -1
        best_score = -1e9
        for i in remaining:
            sim_to_selected = max(
                _jaccard(tokens[i], tokens[s]) for s in selected
            )
            mmr = lambda_ * rel[i] - (1 - lambda_) * sim_to_selected
            if mmr > best_score:
                best_score = mmr
                best_i = i
        if best_i < 0:
            break
        selected.append(best_i)
        remaining.remove(best_i)

    bundle.chunks = [pool[i] for i in selected]
    logger.info("retrieve_mmr_applied",
                before=len(pool), after=len(bundle.chunks), lambda_=lambda_)


async def expand_neighbors(
    bundle: Bundle,
    *,
    vector_store: QdrantVectorStore,
    user_id: str,
    top_k: int = 3,
    project_id: str | None = None,
    document_id: str | None = None,
) -> int:
    """Append same-page ``chunk_index ± 1`` neighbors of the top-K chunks.

    Mitigates information loss across chunk boundaries: a fact that starts at
    the end of chunk N often continues at the start of chunk N+1, but vector
    search may only surface one of them. We fetch and append the missing
    neighbors as additional context (score=0.0), de-duped against existing
    hits. Mutates ``bundle.chunks`` in place. Returns the number of neighbors
    added.
    """
    if not bundle.chunks:
        return 0

    # Existing (doc, page, idx) triples we already have.
    existing: set[tuple[str | None, int | None, int | None]] = {
        (c.doc_name, c.page_num, c.chunk_index) for c in bundle.chunks
    }

    keys: list[tuple[str, int, int]] = []
    seen_keys: set[tuple[str, int, int]] = set()
    for hit in bundle.chunks[:top_k]:
        if not hit.doc_name or hit.page_num is None or hit.chunk_index is None:
            continue
        for delta in (-1, 1):
            ci = hit.chunk_index + delta
            if ci < 0:
                continue
            triple = (hit.doc_name, hit.page_num, ci)
            if triple in existing or triple in seen_keys:
                continue
            seen_keys.add(triple)
            keys.append(triple)

    if not keys:
        return 0

    raw = await vector_store.fetch_chunks_by_keys(
        keys,
        user_id=user_id,
        project_id=project_id,
        document_id=document_id,
    )
    if not raw:
        return 0

    # Convert to SearchResult and append. Mark with score=0.0 so they don't
    # disturb top-score / sort-based assessments.
    added = 0
    for r in raw:
        triple = (r.get("doc_name"), r.get("page_num"), r.get("chunk_index"))
        if triple in existing:
            continue
        existing.add(triple)
        bundle.chunks.append(SearchResult(
            text=r.get("text", ""),
            score=0.0,
            user_id=r.get("user_id"),
            project_id=r.get("project_id"),
            document_id=r.get("document_id"),
            doc_name=r.get("doc_name"),
            filename=r.get("filename"),
            job_id=r.get("job_id"),
            page_num=r.get("page_num"),
            chunk_index=r.get("chunk_index"),
            kind=r.get("kind"),
            table_id=r.get("table_id"),
            table_markdown=r.get("table_markdown"),
            bbox=r.get("bbox"),
            collection="wellsynthai_documents",
        ))
        added += 1

    if added:
        logger.info("retrieve_neighbors_added", count=added,
                    base_top_k=min(top_k, len(bundle.chunks) - added))
    return added


def assessment_to_dict(a: RetrievalAssessment) -> dict[str, Any]:
    return {
        "sufficient": a.sufficient,
        "top_score": round(a.top_score, 4),
        "covered_facets": a.covered_facets,
        "missing_facets": a.missing_facets,
        "reason": a.reason,
    }
