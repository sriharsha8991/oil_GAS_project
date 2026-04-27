"""Qdrant vector store — async collection management, upsert, and search.

Uses ``qdrant-client`` AsyncQdrantClient for non-blocking operations.
Collection is created on first use with cosine distance and the configured
embedding dimensions (default 768 for text-embedding-004).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from qdrant_client import AsyncQdrantClient, models

from app.utils.logging import get_logger

if TYPE_CHECKING:
    from app.config import Settings
    from app.vectorstore.chunker import Chunk

logger = get_logger(__name__)


class QdrantVectorStore:
    """Async Qdrant wrapper for document chunk storage and retrieval."""

    def __init__(self, settings: "Settings") -> None:
        self._client = AsyncQdrantClient(url=settings.qdrant_url)
        self._collection = settings.qdrant_collection
        self._dimensions = settings.embedding_dimensions

    async def ensure_collection(self) -> None:
        """Create the collection if it doesn't exist."""
        exists = await self._client.collection_exists(self._collection)
        if exists:
            logger.info("qdrant_collection_exists", collection=self._collection)
            return

        await self._client.create_collection(
            collection_name=self._collection,
            vectors_config=models.VectorParams(
                size=self._dimensions,
                distance=models.Distance.COSINE,
            ),
        )
        # Create payload indices for common filter fields
        for field in ("job_id", "doc_name", "filename"):
            await self._client.create_payload_index(
                collection_name=self._collection,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        await self._client.create_payload_index(
            collection_name=self._collection,
            field_name="page_num",
            field_schema=models.PayloadSchemaType.INTEGER,
        )

        logger.info(
            "qdrant_collection_created",
            collection=self._collection,
            dimensions=self._dimensions,
        )

    async def upsert_chunks(
        self,
        chunks: list["Chunk"],
        embeddings: list[list[float]],
    ) -> int:
        """Upsert chunk vectors + payloads into Qdrant.

        Returns the number of points upserted.
        """
        if not chunks:
            return 0

        points = [
            models.PointStruct(
                id=str(uuid.uuid4()),
                vector=embedding,
                payload={
                    "text": chunk.text,
                    "doc_name": chunk.doc_name,
                    "job_id": chunk.job_id,
                    "filename": chunk.filename,
                    "page_num": chunk.page_num,
                    "chunk_index": chunk.chunk_index,
                },
            )
            for chunk, embedding in zip(chunks, embeddings)
        ]

        # Qdrant upsert supports batching internally; send in 100-point batches
        batch_size = 100
        total = 0
        for i in range(0, len(points), batch_size):
            batch = points[i : i + batch_size]
            await self._client.upsert(
                collection_name=self._collection,
                points=batch,
            )
            total += len(batch)

        logger.info(
            "qdrant_upserted",
            collection=self._collection,
            points=total,
        )
        return total

    async def search(
        self,
        query_vector: list[float],
        *,
        limit: int = 10,
        job_id: str | None = None,
        doc_name: str | None = None,
        page_num: int | None = None,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic similarity search over document chunks.

        Args:
            query_vector: Embedding of the search query.
            limit: Max results to return.
            job_id: Filter to a specific ingestion job.
            doc_name: Filter to a specific document.
            page_num: Filter to a specific page.
            score_threshold: Minimum similarity score.

        Returns:
            List of dicts with ``text``, ``score``, and metadata fields.
        """
        must_filters: list[models.Condition] = []
        if job_id:
            must_filters.append(
                models.FieldCondition(
                    key="job_id",
                    match=models.MatchValue(value=job_id),
                )
            )
        if doc_name:
            must_filters.append(
                models.FieldCondition(
                    key="doc_name",
                    match=models.MatchValue(value=doc_name),
                )
            )
        if page_num is not None:
            must_filters.append(
                models.FieldCondition(
                    key="page_num",
                    match=models.MatchValue(value=page_num),
                )
            )

        query_filter = models.Filter(must=must_filters) if must_filters else None

        results = await self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            limit=limit,
            query_filter=query_filter,
            score_threshold=score_threshold,
        )

        return [
            {
                "text": point.payload.get("text", ""),
                "score": point.score,
                "doc_name": point.payload.get("doc_name"),
                "filename": point.payload.get("filename"),
                "job_id": point.payload.get("job_id"),
                "page_num": point.payload.get("page_num"),
                "chunk_index": point.payload.get("chunk_index"),
            }
            for point in results.points
        ]

    async def delete_by_job(self, job_id: str) -> None:
        """Delete all vectors belonging to a job (for re-ingestion)."""
        await self._client.delete(
            collection_name=self._collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="job_id",
                            match=models.MatchValue(value=job_id),
                        )
                    ]
                )
            ),
        )
        logger.info("qdrant_deleted_job", job_id=job_id)

    async def close(self) -> None:
        """Close the underlying client."""
        await self._client.close()
