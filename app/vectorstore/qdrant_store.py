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
        """Create all required collections if they don't exist."""
        await self._ensure_single_collection(
            self._collection,
            payload_indices={
                "job_id": models.PayloadSchemaType.KEYWORD,
                "doc_name": models.PayloadSchemaType.KEYWORD,
                "filename": models.PayloadSchemaType.KEYWORD,
                "page_num": models.PayloadSchemaType.INTEGER,
                "kind": models.PayloadSchemaType.KEYWORD,
            },
        )
        # Entity facts collection
        await self._ensure_single_collection(
            "wellsynthai_entities",
            payload_indices={
                "job_id": models.PayloadSchemaType.KEYWORD,
                "doc_name": models.PayloadSchemaType.KEYWORD,
                "entity_type": models.PayloadSchemaType.KEYWORD,
                "canonical_id": models.PayloadSchemaType.KEYWORD,
                "unit": models.PayloadSchemaType.KEYWORD,
                "page_num": models.PayloadSchemaType.INTEGER,
            },
        )
        # Image descriptions collection
        await self._ensure_single_collection(
            "wellsynthai_images",
            payload_indices={
                "job_id": models.PayloadSchemaType.KEYWORD,
                "doc_name": models.PayloadSchemaType.KEYWORD,
                "image_type": models.PayloadSchemaType.KEYWORD,
                "page_num": models.PayloadSchemaType.INTEGER,
            },
        )

    async def _ensure_single_collection(
        self,
        name: str,
        payload_indices: dict[str, models.PayloadSchemaType],
    ) -> None:
        """Create a single collection with payload indices if it doesn't exist."""
        exists = await self._client.collection_exists(name)
        if exists:
            logger.info("qdrant_collection_exists", collection=name)
            return

        await self._client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(
                size=self._dimensions,
                distance=models.Distance.COSINE,
            ),
        )
        for field_name, schema_type in payload_indices.items():
            await self._client.create_payload_index(
                collection_name=name,
                field_name=field_name,
                field_schema=schema_type,
            )

        logger.info(
            "qdrant_collection_created",
            collection=name,
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
                    "kind": chunk.kind,
                    "table_id": chunk.table_id,
                    "table_markdown": chunk.table_markdown,
                    "bbox": chunk.bbox,
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

    async def fetch_chunks_by_keys(
        self,
        keys: list[tuple[str, int, int]],
        *,
        job_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Look up chunks by exact ``(doc_name, page_num, chunk_index)`` triples.

        Used for adjacent-chunk context expansion: given a top hit, fetch its
        immediate neighbors so information that bleeds across chunk boundaries
        is preserved when shown to the synthesizer.

        Returns dicts in the same shape as :meth:`search` (with ``score=0.0``).
        """
        if not keys:
            return []

        should: list[models.Filter] = []
        for doc_name, page_num, chunk_index in keys:
            must: list[models.Condition] = [
                models.FieldCondition(key="doc_name",
                                      match=models.MatchValue(value=doc_name)),
                models.FieldCondition(key="page_num",
                                      match=models.MatchValue(value=page_num)),
                models.FieldCondition(key="chunk_index",
                                      match=models.MatchValue(value=chunk_index)),
            ]
            if job_id:
                must.append(models.FieldCondition(
                    key="job_id", match=models.MatchValue(value=job_id),
                ))
            should.append(models.Filter(must=must))

        scrolled, _ = await self._client.scroll(
            collection_name=self._collection,
            scroll_filter=models.Filter(should=should),
            limit=max(1, len(keys)),
            with_payload=True,
            with_vectors=False,
        )
        return [
            {
                "text": p.payload.get("text", ""),
                "score": 0.0,
                "doc_name": p.payload.get("doc_name"),
                "filename": p.payload.get("filename"),
                "job_id": p.payload.get("job_id"),
                "page_num": p.payload.get("page_num"),
                "chunk_index": p.payload.get("chunk_index"),
                "kind": p.payload.get("kind"),
                "table_id": p.payload.get("table_id"),
                "table_markdown": p.payload.get("table_markdown"),
                "bbox": p.payload.get("bbox"),
            }
            for p in scrolled
        ]

    async def delete_by_job(self, job_id: str) -> None:
        """Delete all vectors belonging to a job (for re-ingestion).

        Purges the main chunk collection AND the entity + image
        collections so re-ingestion of the same job_id is fully idempotent.
        """
        selector = models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="job_id",
                        match=models.MatchValue(value=job_id),
                    )
                ]
            )
        )
        for collection in (
            self._collection,
            "wellsynthai_entities",
            "wellsynthai_images",
        ):
            try:
                await self._client.delete(
                    collection_name=collection,
                    points_selector=selector,
                )
            except Exception:
                logger.warning(
                    "qdrant_delete_skipped",
                    collection=collection,
                    job_id=job_id,
                    exc_info=True,
                )
        logger.info("qdrant_deleted_job", job_id=job_id)

    async def close(self) -> None:
        """Close the underlying client."""
        await self._client.close()
