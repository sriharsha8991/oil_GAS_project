"""Search routes — semantic search over ingested document chunks."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth.deps import AuthUser, current_user
from app.utils.logging import get_logger
from app.vectorstore.embedder import GeminiEmbedder
from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)
router = APIRouter(prefix="/search", tags=["search"])


def _get_clients(request: Request) -> tuple[GeminiEmbedder, QdrantVectorStore]:
    """Pull the process-wide singletons from app.state (built in lifespan)."""
    embedder: GeminiEmbedder | None = getattr(request.app.state, "embedder", None)
    store: QdrantVectorStore | None = getattr(request.app.state, "qdrant", None)
    if embedder is None or store is None:
        raise HTTPException(status_code=503, detail="Gemini API key not configured")
    return embedder, store


@router.get("", summary="Semantic search across ingested documents")
async def search(
    request: Request,
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(10, ge=1, le=100, description="Max results"),
    project_id: str | None = Query(None, description="Filter by project"),
    document_id: str | None = Query(None, description="Filter by document"),
    page_num: int | None = Query(None, ge=0, description="Filter by page number"),
    score_threshold: float | None = Query(None, ge=0.0, le=1.0, description="Min similarity score"),
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    """Embed the query and search Qdrant for similar document chunks."""
    embedder, store = _get_clients(request)

    query_vector = await embedder.embed_query(q)
    results = await store.search(
        query_vector,
        user_id=user.id,
        limit=limit,
        project_id=project_id,
        document_id=document_id,
        page_num=page_num,
        score_threshold=score_threshold,
    )

    return {
        "query": q,
        "count": len(results),
        "results": results,
    }
