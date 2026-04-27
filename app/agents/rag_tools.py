"""RAG tool — semantic search over ingested document chunks.

Wraps the existing vectorstore layer (GeminiEmbedder + QdrantVectorStore)
as a callable tool for the RAG agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.utils.logging import get_logger
from app.vectorstore.embedder import GeminiEmbedder
from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)


@dataclass
class SearchResult:
    """Single search hit returned by the RAG tool."""

    text: str
    score: float
    doc_name: str | None
    filename: str | None
    job_id: str | None
    page_num: int | None
    chunk_index: int | None


async def search_documents(
    query: str,
    *,
    embedder: GeminiEmbedder,
    vector_store: QdrantVectorStore,
    limit: int = 10,
    job_id: str | None = None,
    doc_name: str | None = None,
    score_threshold: float = 0.3,
) -> list[SearchResult]:
    """Embed *query* and retrieve the top-k matching chunks from Qdrant.

    This is the single tool exposed to the RAG agent.  It returns
    structured ``SearchResult`` objects so the agent can cite sources.
    """
    logger.info("rag_tool_search", query=query, limit=limit, job_id=job_id, doc_name=doc_name)

    query_vector = await embedder.embed_query(query)
    raw_results = await vector_store.search(
        query_vector,
        limit=limit,
        job_id=job_id,
        doc_name=doc_name,
        score_threshold=score_threshold,
    )

    results = [
        SearchResult(
            text=r["text"],
            score=r["score"],
            doc_name=r.get("doc_name"),
            filename=r.get("filename"),
            job_id=r.get("job_id"),
            page_num=r.get("page_num"),
            chunk_index=r.get("chunk_index"),
        )
        for r in raw_results
    ]

    logger.info("rag_tool_results", query=query, count=len(results))
    return results


def format_search_results(results: list[SearchResult]) -> str:
    """Format search results into a text block for the LLM context."""
    if not results:
        return "No relevant documents found."

    parts: list[str] = []
    for i, r in enumerate(results, 1):
        source = f"[Source {i}] "
        if r.doc_name:
            source += f"Document: {r.doc_name}"
        if r.page_num is not None:
            source += f", Page {r.page_num + 1}"
        source += f" (score: {r.score:.3f})"
        parts.append(f"{source}\n{r.text}")

    return "\n\n---\n\n".join(parts)
