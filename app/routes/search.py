"""Search routes — semantic search over ingested document chunks."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.config import get_settings
from app.utils.logging import get_logger
from app.vectorstore.embedder import GeminiEmbedder
from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)
router = APIRouter(prefix="/search", tags=["search"])

# Lazy singletons — initialized on first request via _get_clients().
_embedder: GeminiEmbedder | None = None
_vector_store: QdrantVectorStore | None = None


def _get_clients() -> tuple[GeminiEmbedder, QdrantVectorStore]:
    """Return (embedder, vector_store), creating them once."""
    global _embedder, _vector_store  # noqa: PLW0603
    if _embedder is None:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise HTTPException(status_code=503, detail="Gemini API key not configured")
        _embedder = GeminiEmbedder(settings)
        _vector_store = QdrantVectorStore(settings)
    return _embedder, _vector_store  # type: ignore[return-value]


@router.get("", summary="Semantic search across ingested documents")
async def search(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(10, ge=1, le=100, description="Max results"),
    job_id: str | None = Query(None, description="Filter by job ID"),
    doc_name: str | None = Query(None, description="Filter by document name"),
    page_num: int | None = Query(None, ge=0, description="Filter by page number"),
    score_threshold: float | None = Query(None, ge=0.0, le=1.0, description="Min similarity score"),
) -> dict[str, Any]:
    """Embed the query and search Qdrant for similar document chunks."""
    embedder, store = _get_clients()

    query_vector = await embedder.embed_query(q)
    results = await store.search(
        query_vector,
        limit=limit,
        job_id=job_id,
        doc_name=doc_name,
        page_num=page_num,
        score_threshold=score_threshold,
    )

    return {
        "query": q,
        "count": len(results),
        "results": results,
    }
