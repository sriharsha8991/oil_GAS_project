"""RAG tools — three search tools exposed directly to the RAG agent.

Each tool targets a specific Qdrant collection:
  - search_chunks:   text chunk retrieval from wellsynthai_documents
  - search_entities: structured entity retrieval from wellsynthai_entities
  - search_images:   image/diagram retrieval from wellsynthai_images

The LLM decides which tool to call (no separate classifier needed).
Reranking via tier weights is applied inside each tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qdrant_client import models

from app.db.engine import get_session_factory
from app.db.queries.document_meta import fetch_document_meta
from app.retrieval.reranker import RankedResult, rerank
from app.utils.logging import get_logger
from app.vectorstore.embedder import GeminiEmbedder
from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)

# Collection names
_CHUNKS_COLLECTION = "wellsynthai_documents"
_ENTITY_COLLECTION = "wellsynthai_entities"
_IMAGE_COLLECTION = "wellsynthai_images"


@dataclass
class SearchResult:
    """Single search hit returned by a RAG tool."""

    text: str
    score: float
    user_id: str | None = None
    project_id: str | None = None
    document_id: str | None = None
    doc_name: str | None = None
    filename: str | None = None
    job_id: str | None = None
    page_num: int | None = None
    chunk_index: int | None = None
    # Chunk-specific
    kind: str | None = None              # "narrative" | "table"
    table_id: str | None = None
    table_markdown: str | None = None
    # Entity-specific
    entity_type: str | None = None
    canonical_id: str | None = None
    canonical_value: str | None = None
    unit: str | None = None
    occurrences: int | None = None
    mentions: list[dict[str, Any]] | None = None
    # Image-specific
    image_type: str | None = None
    description: str | None = None
    # Spatial
    bbox: list[int] | None = None
    # Source
    collection: str = ""


# -------------------------------------------------------------------
# Shared helpers
# -------------------------------------------------------------------

def _scope_filters(
    *,
    user_id: str,
    project_id: str | None,
    document_id: str | None,
) -> list[models.Condition]:
    """Required tenant filter (user_id) plus optional project/document scope."""
    filters: list[models.Condition] = [
        models.FieldCondition(
            key="user_id", match=models.MatchValue(value=user_id)
        ),
    ]
    if project_id:
        filters.append(
            models.FieldCondition(
                key="project_id", match=models.MatchValue(value=project_id)
            )
        )
    if document_id:
        filters.append(
            models.FieldCondition(
                key="document_id", match=models.MatchValue(value=document_id)
            )
        )
    return filters


async def _query_collection(
    client,
    collection: str,
    query_vector: list[float],
    *,
    limit: int = 10,
    query_filter: models.Filter | None = None,
    score_threshold: float = 0.3,
) -> list[dict[str, Any]]:
    """Run a single Qdrant query_points call and return raw dicts."""
    exists = await client.collection_exists(collection)
    if not exists:
        return []

    results = await client.query_points(
        collection_name=collection,
        query=query_vector,
        limit=limit,
        query_filter=query_filter,
        score_threshold=score_threshold,
    )

    return [
        {
            "text": point.payload.get("text", ""),
            "score": point.score,
            **{k: v for k, v in point.payload.items() if k != "text"},
        }
        for point in results.points
    ]


def _to_search_results(ranked: list[RankedResult]) -> list[SearchResult]:
    """Convert RankedResult list to SearchResult list."""
    return [
        SearchResult(
            text=r.text,
            score=r.adjusted_score,
            user_id=r.user_id or None,
            project_id=r.project_id or None,
            document_id=r.document_id or None,
            doc_name=r.doc_name or None,
            filename=r.filename or None,
            job_id=r.job_id or None,
            page_num=r.page_num if r.page_num else None,
            chunk_index=r.chunk_index if r.chunk_index else None,
            kind=r.kind or None,
            table_id=r.table_id or None,
            table_markdown=r.table_markdown or None,
            entity_type=r.entity_type or None,
            canonical_id=r.canonical_id or None,
            canonical_value=r.canonical_value or None,
            unit=r.unit or None,
            occurrences=r.occurrences or None,
            mentions=r.mentions,
            image_type=r.image_type or None,
            description=r.description or None,
            bbox=r.bbox,
            collection=r.collection,
        )
        for r in ranked
    ]


async def _apply_governance(
    raw: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach ``artifact_class`` + ``confidentiality`` from Postgres and drop
    results from superseded documents.

    Performed after Qdrant search but before reranking, so per-result tier
    weights and supersession take effect on every query (no Qdrant
    backfill required when an artifact class is edited).
    """
    if not raw:
        return raw

    document_ids = {r.get("document_id") for r in raw if r.get("document_id")}
    if not document_ids:
        return raw

    Session = get_session_factory()
    async with Session() as sess:
        meta_by_id = await fetch_document_meta(sess, document_ids)

    enriched: list[dict[str, Any]] = []
    dropped_superseded = 0
    for r in raw:
        meta = meta_by_id.get(str(r.get("document_id") or ""))
        if meta is None:
            # Unknown document (orphaned vector) — keep with default weight
            enriched.append(r)
            continue
        if meta.is_superseded:
            dropped_superseded += 1
            continue
        enriched.append({
            **r,
            "artifact_class": meta.artifact_class,
            "confidentiality": meta.confidentiality,
            "revision": meta.revision,
        })

    if dropped_superseded:
        logger.info(
            "rag_dropped_superseded",
            dropped=dropped_superseded,
            kept=len(enriched),
        )
    return enriched


# -------------------------------------------------------------------
# Tool 1: search_chunks  (text chunks from documents)
# -------------------------------------------------------------------

async def search_chunks(
    query: str,
    *,
    embedder: GeminiEmbedder,
    vector_store: QdrantVectorStore,
    user_id: str,
    limit: int = 10,
    project_id: str | None = None,
    document_id: str | None = None,
    score_threshold: float = 0.3,
    kinds: list[str] | None = None,
) -> list[SearchResult]:
    """Search text chunks from ingested documents.

    Use this for general factual questions about well operations,
    drilling summaries, procedures, or any narrative content.
    ``kinds`` optionally restricts to {"narrative", "table"}.
    """
    logger.info("tool_search_chunks", query=query, limit=limit, kinds=kinds)

    query_vector = await embedder.embed_query(query)
    filters = _scope_filters(
        user_id=user_id, project_id=project_id, document_id=document_id
    )
    if kinds:
        filters.append(
            models.FieldCondition(key="kind", match=models.MatchAny(any=list(kinds)))
        )
    qf = models.Filter(must=filters)

    raw = await _query_collection(
        vector_store._client, _CHUNKS_COLLECTION, query_vector,
        limit=limit, query_filter=qf, score_threshold=score_threshold,
    )
    raw = await _apply_governance(raw)
    ranked = rerank(raw, collection=_CHUNKS_COLLECTION)
    results = _to_search_results(ranked)

    logger.info("tool_search_chunks_done", query=query, count=len(results))
    return results


# -------------------------------------------------------------------
# Tool 2: search_entities  (structured entity facts)
# -------------------------------------------------------------------

async def search_entities(
    query: str,
    *,
    embedder: GeminiEmbedder,
    vector_store: QdrantVectorStore,
    user_id: str,
    limit: int = 10,
    project_id: str | None = None,
    document_id: str | None = None,
    entity_type: str | None = None,
    unit: str | None = None,
    score_threshold: float = 0.3,
) -> list[SearchResult]:
    """Search structured entities (depths, formations, NPT events, pressures, etc.).

    Use this for specific entity lookups like "what was the mud weight",
    "total depth of well ABC-1", "NPT events", "formation tops", etc.
    """
    logger.info(
        "tool_search_entities",
        query=query, entity_type=entity_type, unit=unit, limit=limit,
    )

    query_vector = await embedder.embed_query(query)
    filters = _scope_filters(
        user_id=user_id, project_id=project_id, document_id=document_id
    )
    if entity_type:
        filters.append(
            models.FieldCondition(key="entity_type", match=models.MatchValue(value=entity_type))
        )
    if unit:
        filters.append(
            models.FieldCondition(key="unit", match=models.MatchValue(value=unit))
        )
    qf = models.Filter(must=filters)

    raw = await _query_collection(
        vector_store._client, _ENTITY_COLLECTION, query_vector,
        limit=limit, query_filter=qf, score_threshold=score_threshold,
    )
    raw = await _apply_governance(raw)
    ranked = rerank(raw, collection=_ENTITY_COLLECTION)
    results = _to_search_results(ranked)

    logger.info("tool_search_entities_done", query=query, count=len(results))
    return results


# -------------------------------------------------------------------
# Tool 3: search_images  (image descriptions / diagrams)
# -------------------------------------------------------------------

async def search_images(
    query: str,
    *,
    embedder: GeminiEmbedder,
    vector_store: QdrantVectorStore,
    user_id: str,
    limit: int = 5,
    project_id: str | None = None,
    document_id: str | None = None,
    score_threshold: float = 0.3,
    image_types: list[str] | None = None,
) -> list[SearchResult]:
    """Search image descriptions, diagrams, wellbore schematics, and figures.

    Use this when the user asks about diagrams, charts, wellbore
    schematics, log strips, cross-sections, photos, or any visual content.
    ``image_types`` optionally restricts to {schematic, log_strip, chart, ...}.
    """
    logger.info(
        "tool_search_images",
        query=query, limit=limit, image_types=image_types,
    )

    query_vector = await embedder.embed_query(query)
    filters = _scope_filters(
        user_id=user_id, project_id=project_id, document_id=document_id
    )
    if image_types:
        filters.append(
            models.FieldCondition(
                key="image_type", match=models.MatchAny(any=list(image_types))
            )
        )
    qf = models.Filter(must=filters)

    raw = await _query_collection(
        vector_store._client, _IMAGE_COLLECTION, query_vector,
        limit=limit, query_filter=qf, score_threshold=score_threshold,
    )
    raw = await _apply_governance(raw)
    ranked = rerank(raw, collection=_IMAGE_COLLECTION)
    results = _to_search_results(ranked)

    logger.info("tool_search_images_done", query=query, count=len(results))
    return results


# -------------------------------------------------------------------
# Formatting (for feeding results back into the agent loop)
# -------------------------------------------------------------------

def format_search_results(results: list[SearchResult]) -> str:
    """Format search results into a text block for the LLM context."""
    if not results:
        return "No relevant results found."

    parts: list[str] = []
    for i, r in enumerate(results, 1):
        source = f"[Source {i}] "
        if r.collection:
            source += f"({r.collection}) "
        if r.doc_name:
            source += f"Document: {r.doc_name}"
        if r.page_num is not None:
            source += f", Page {r.page_num + 1}"
        if r.entity_type:
            source += f", Entity: {r.entity_type}"
        if r.canonical_value:
            source += f" = {r.canonical_value}"
        source += f" (score: {r.score:.3f})"
        parts.append(f"{source}\n{r.text}")

    return "\n\n---\n\n".join(parts)
