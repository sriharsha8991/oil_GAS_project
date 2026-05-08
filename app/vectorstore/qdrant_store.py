"""Qdrant vector store — async collection management, upsert, and search.

Every point's payload carries a tenant-scoping block:

    {
        "user_id":       <auth.users.id>,
        "role":          <users_profile.role at index time>,
        "organization":  <users_profile.organization at index time>,
        "project_id":    <projects.id>,
        "document_id":   <documents.id>,
        ...type-specific fields (text, page_num, ...)
    }

All retrieval queries MUST filter by ``user_id``. Cross-user leakage
without that filter is a security bug.
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


CHUNKS_COLLECTION_DEFAULT = "wellsynthai_documents"
ENTITIES_COLLECTION = "wellsynthai_entities"
IMAGES_COLLECTION = "wellsynthai_images"


_TENANT_INDICES: dict[str, models.PayloadSchemaType] = {
    "user_id": models.PayloadSchemaType.KEYWORD,
    "project_id": models.PayloadSchemaType.KEYWORD,
    "document_id": models.PayloadSchemaType.KEYWORD,
    "organization": models.PayloadSchemaType.KEYWORD,
    "role": models.PayloadSchemaType.KEYWORD,
}


class QdrantVectorStore:
    """Async Qdrant wrapper for document chunk storage and retrieval."""

    def __init__(self, settings: "Settings") -> None:
        self._client = AsyncQdrantClient(url=settings.qdrant_url)
        self._collection = settings.qdrant_collection
        self._dimensions = settings.embedding_dimensions

    # ------------------------------------------------------------------
    # Collection lifecycle
    # ------------------------------------------------------------------

    async def ensure_collection(self) -> None:
        """Create all required collections if they don't exist."""
        await self._ensure_single_collection(
            self._collection,
            payload_indices={
                **_TENANT_INDICES,
                "doc_name": models.PayloadSchemaType.KEYWORD,
                "filename": models.PayloadSchemaType.KEYWORD,
                "page_num": models.PayloadSchemaType.INTEGER,
                "kind": models.PayloadSchemaType.KEYWORD,
            },
        )
        await self._ensure_single_collection(
            ENTITIES_COLLECTION,
            payload_indices={
                **_TENANT_INDICES,
                "doc_name": models.PayloadSchemaType.KEYWORD,
                "entity_type": models.PayloadSchemaType.KEYWORD,
                "canonical_id": models.PayloadSchemaType.KEYWORD,
                "unit": models.PayloadSchemaType.KEYWORD,
                "page_num": models.PayloadSchemaType.INTEGER,
            },
        )
        await self._ensure_single_collection(
            IMAGES_COLLECTION,
            payload_indices={
                **_TENANT_INDICES,
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

    async def reset_all(self) -> None:
        """Drop and recreate all three collections. Destructive."""
        for name in (
            self._collection,
            ENTITIES_COLLECTION,
            IMAGES_COLLECTION,
        ):
            try:
                if await self._client.collection_exists(name):
                    await self._client.delete_collection(name)
                    logger.info("qdrant_collection_deleted", collection=name)
            except Exception:
                logger.warning("qdrant_collection_delete_failed", collection=name)
        await self.ensure_collection()

    # ------------------------------------------------------------------
    # Upsert (chunks)
    # ------------------------------------------------------------------

    async def upsert_chunks(
        self,
        chunks: list["Chunk"],
        embeddings: list[list[float]],
    ) -> int:
        if not chunks:
            return 0

        points = [
            models.PointStruct(
                id=str(uuid.uuid4()),
                vector=embedding,
                payload={
                    "text": chunk.text,
                    "user_id": chunk.user_id,
                    "role": chunk.role,
                    "organization": chunk.organization,
                    "project_id": chunk.project_id,
                    "document_id": chunk.document_id,
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

        batch_size = 100
        total = 0
        for i in range(0, len(points), batch_size):
            batch = points[i : i + batch_size]
            await self._client.upsert(
                collection_name=self._collection,
                points=batch,
            )
            total += len(batch)

        logger.info("qdrant_upserted", collection=self._collection, points=total)
        return total

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    @staticmethod
    def _scope_filter(
        *,
        user_id: str,
        project_id: str | None = None,
        document_id: str | None = None,
        extra: list[models.Condition] | None = None,
    ) -> models.Filter:
        must: list[models.Condition] = [
            models.FieldCondition(
                key="user_id", match=models.MatchValue(value=user_id)
            ),
        ]
        if project_id:
            must.append(
                models.FieldCondition(
                    key="project_id", match=models.MatchValue(value=project_id)
                )
            )
        if document_id:
            must.append(
                models.FieldCondition(
                    key="document_id", match=models.MatchValue(value=document_id)
                )
            )
        if extra:
            must.extend(extra)
        return models.Filter(must=must)

    async def search(
        self,
        query_vector: list[float],
        *,
        user_id: str,
        limit: int = 10,
        project_id: str | None = None,
        document_id: str | None = None,
        page_num: int | None = None,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        extra: list[models.Condition] = []
        if page_num is not None:
            extra.append(
                models.FieldCondition(
                    key="page_num", match=models.MatchValue(value=page_num)
                )
            )

        query_filter = self._scope_filter(
            user_id=user_id,
            project_id=project_id,
            document_id=document_id,
            extra=extra,
        )

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
                "user_id": point.payload.get("user_id"),
                "project_id": point.payload.get("project_id"),
                "document_id": point.payload.get("document_id"),
                "doc_name": point.payload.get("doc_name"),
                "filename": point.payload.get("filename"),
                "job_id": point.payload.get("job_id"),
                "page_num": point.payload.get("page_num"),
                "chunk_index": point.payload.get("chunk_index"),
                "kind": point.payload.get("kind"),
                "table_id": point.payload.get("table_id"),
                "table_markdown": point.payload.get("table_markdown"),
                "bbox": point.payload.get("bbox"),
            }
            for point in results.points
        ]

    async def fetch_chunks_by_keys(
        self,
        keys: list[tuple[str, int, int]],
        *,
        user_id: str,
        project_id: str | None = None,
        document_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Look up chunks by ``(doc_name, page_num, chunk_index)`` triples.

        Always scoped to ``user_id`` so adjacent-chunk fetches cannot leak
        cross-tenant data.
        """
        if not keys:
            return []

        should: list[models.Filter] = []
        for doc_name, page_num, chunk_index in keys:
            must: list[models.Condition] = [
                models.FieldCondition(
                    key="user_id", match=models.MatchValue(value=user_id)
                ),
                models.FieldCondition(
                    key="doc_name", match=models.MatchValue(value=doc_name)
                ),
                models.FieldCondition(
                    key="page_num", match=models.MatchValue(value=page_num)
                ),
                models.FieldCondition(
                    key="chunk_index",
                    match=models.MatchValue(value=chunk_index),
                ),
            ]
            if project_id:
                must.append(
                    models.FieldCondition(
                        key="project_id",
                        match=models.MatchValue(value=project_id),
                    )
                )
            if document_id:
                must.append(
                    models.FieldCondition(
                        key="document_id",
                        match=models.MatchValue(value=document_id),
                    )
                )
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
                "user_id": p.payload.get("user_id"),
                "project_id": p.payload.get("project_id"),
                "document_id": p.payload.get("document_id"),
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

    # ------------------------------------------------------------------
    # Deletion (used by re-ingestion + delete-document/project endpoints)
    # ------------------------------------------------------------------

    async def _delete_by_field(self, field: str, value: str) -> None:
        selector = models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key=field, match=models.MatchValue(value=value)
                    )
                ]
            )
        )
        for collection in (
            self._collection,
            ENTITIES_COLLECTION,
            IMAGES_COLLECTION,
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
                    field=field,
                    value=value,
                    exc_info=True,
                )

    async def delete_by_document(self, document_id: str) -> None:
        await self._delete_by_field("document_id", document_id)
        logger.info("qdrant_deleted_document", document_id=document_id)

    async def delete_by_project(self, project_id: str) -> None:
        await self._delete_by_field("project_id", project_id)
        logger.info("qdrant_deleted_project", project_id=project_id)

    async def delete_by_user(self, user_id: str) -> None:
        await self._delete_by_field("user_id", user_id)
        logger.info("qdrant_deleted_user", user_id=user_id)

    # ------------------------------------------------------------------
    # Counting helpers (used by document outline tool)
    # ------------------------------------------------------------------

    async def count_points(
        self,
        *,
        collection: str,
        user_id: str,
        document_id: str,
        kind: str | None = None,
    ) -> int:
        """Return the exact point count matching the scope filter."""
        extras: list[models.Condition] = []
        if kind is not None:
            extras.append(
                models.FieldCondition(
                    key="kind", match=models.MatchValue(value=kind)
                )
            )
        try:
            res = await self._client.count(
                collection_name=collection,
                count_filter=self._scope_filter(
                    user_id=user_id,
                    document_id=document_id,
                    extra=extras,
                ),
                exact=True,
            )
            return int(getattr(res, "count", 0) or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("qdrant_count_failed",
                           collection=collection, error=str(exc))
            return 0

    # ------------------------------------------------------------------
    # Table fetch (used by fetch_table tool)
    # ------------------------------------------------------------------

    async def fetch_table_chunks(
        self,
        *,
        user_id: str,
        document_id: str,
        table_id: str | None = None,
        page_num: int | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """Return table chunks (kind=table) matching scope, with full markdown.

        Owner-scoped via ``_scope_filter``. Filters further by ``table_id``
        (preferred) or ``page_num`` (fallback when the model doesn't know
        the table_id). Returns a list of {table_id, page_num, doc_name,
        table_markdown, bbox, continues_on_next_page} dicts.
        """
        extras: list[models.Condition] = [
            models.FieldCondition(
                key="kind", match=models.MatchValue(value="table")
            ),
        ]
        if table_id:
            extras.append(
                models.FieldCondition(
                    key="table_id", match=models.MatchValue(value=table_id)
                )
            )
        if page_num is not None:
            extras.append(
                models.FieldCondition(
                    key="page_num", match=models.MatchValue(value=page_num)
                )
            )
        try:
            scrolled, _ = await self._client.scroll(
                collection_name=CHUNKS_COLLECTION_DEFAULT,
                scroll_filter=self._scope_filter(
                    user_id=user_id,
                    document_id=document_id,
                    extra=extras,
                ),
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("qdrant_fetch_table_failed",
                           document_id=document_id, table_id=table_id,
                           error=str(exc))
            return []

        out: list[dict[str, Any]] = []
        for p in scrolled:
            payload = p.payload or {}
            md = payload.get("table_markdown") or ""
            if not md:
                continue
            out.append({
                "table_id": payload.get("table_id"),
                "page_num": payload.get("page_num"),
                "doc_name": payload.get("doc_name"),
                "table_markdown": md,
                "bbox": payload.get("bbox"),
                "continues_on_next_page": bool(
                    (payload.get("extra") or {}).get("continues_on_next_page")
                ),
            })
        return out

    async def close(self) -> None:
        await self._client.close()
