"""Entity indexer — embeds and upserts resolved entities into Qdrant.

Each resolved entity becomes one point in the ``entities_facts`` collection.
The embedding is built from a canonical text representation:
    "{entity_type} {canonical_value} {unit} {context}"

This enables filtered retrieval like "all NPT > 100h in Eagle Ford"
without LLM involvement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.utils.logging import get_logger

if TYPE_CHECKING:
    from app.vectorstore.embedder import GeminiEmbedder
    from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)

ENTITY_COLLECTION = "wellsynthai_entities"


@dataclass
class EntityPoint:
    """A single entity ready for Qdrant upsert (deduplicated per document)."""
    text: str               # embedding source text
    user_id: str
    role: str
    organization: str | None
    project_id: str
    document_id: str
    entity_type: str
    canonical_id: str
    canonical_value: str
    unit: str
    context: str
    doc_name: str
    job_id: str
    filename: str
    page_num: int            # first page where this entity was seen
    resolution: str          # matched | created | normalized
    bbox: list[int] | None = None
    mentions: list[dict[str, Any]] = field(default_factory=list)
    occurrences: int = 1


def build_entity_points(
    resolved_by_page: dict[int, list[dict]],
    *,
    user_id: str,
    role: str,
    organization: str | None,
    project_id: str,
    document_id: str,
    doc_name: str,
    job_id: str,
    filename: str,
) -> list[EntityPoint]:
    """Convert resolved entity dicts into deduplicated EntityPoint objects.

    The same canonical entity (e.g. well_id "Sajaa-1") often appears on
    dozens of pages; embedding each occurrence inflates the entity collection
    and pollutes retrieval with near-duplicate vectors. We collapse to one
    point per (entity_type, canonical_id|value, unit) tuple and aggregate
    every mention's (page_num, bbox) into the payload.
    """
    bucket: dict[tuple[str, str, str], EntityPoint] = {}

    for page_num, entities in sorted(resolved_by_page.items()):
        for ent in entities:
            entity_type = ent.get("type", "other") or "other"
            canonical_value = (
                ent.get("canonical_value", "") or ent.get("value", "") or ""
            ).strip()
            if not canonical_value:
                continue
            unit = (ent.get("unit", "") or "").strip()
            canonical_id = ent.get("canonical_id", "") or canonical_value.lower()
            key = (entity_type, canonical_id, unit)

            mention = {
                "page_num": page_num,
                "bbox": ent.get("bbox"),
                "context": ent.get("context", ""),
            }

            existing = bucket.get(key)
            if existing is not None:
                existing.occurrences += 1
                existing.mentions.append(mention)
                continue

            context = ent.get("context", "") or ""
            parts = [entity_type, canonical_value]
            if unit:
                parts.append(unit)
            if context:
                parts.append(context)
            text = " ".join(p for p in parts if p)

            bucket[key] = EntityPoint(
                text=text,
                user_id=user_id,
                role=role,
                organization=organization,
                project_id=project_id,
                document_id=document_id,
                entity_type=entity_type,
                canonical_id=canonical_id,
                canonical_value=canonical_value,
                unit=unit,
                context=context,
                doc_name=doc_name,
                job_id=job_id,
                filename=filename,
                page_num=page_num,
                resolution=ent.get("resolution", ""),
                bbox=ent.get("bbox"),
                mentions=[mention],
                occurrences=1,
            )

    points = list(bucket.values())
    logger.info(
        "entity_points_built",
        raw_count=sum(len(v) for v in resolved_by_page.values()),
        deduped_count=len(points),
    )
    return points


async def upsert_entity_points(
    points: list[EntityPoint],
    embedder: "GeminiEmbedder",
    vector_store: "QdrantVectorStore",
) -> int:
    """Embed and upsert entity points into the entities_facts collection.

    Returns the number of points upserted.
    """
    if not points:
        return 0

    texts = [p.text for p in points]
    embeddings = await embedder.embed_texts(texts, task_type="RETRIEVAL_DOCUMENT")

    from qdrant_client import models
    import uuid

    qdrant_points = [
        models.PointStruct(
            id=str(uuid.uuid4()),
            vector=embedding,
            payload={
                "text": point.text,
                "user_id": point.user_id,
                "role": point.role,
                "organization": point.organization,
                "project_id": point.project_id,
                "document_id": point.document_id,
                "entity_type": point.entity_type,
                "canonical_id": point.canonical_id,
                "canonical_value": point.canonical_value,
                "unit": point.unit,
                "context": point.context,
                "doc_name": point.doc_name,
                "job_id": point.job_id,
                "filename": point.filename,
                "page_num": point.page_num,
                "resolution": point.resolution,
                "bbox": point.bbox,
                "occurrences": point.occurrences,
                "mentions": point.mentions,
            },
        )
        for point, embedding in zip(points, embeddings)
    ]

    batch_size = 100
    total = 0
    for i in range(0, len(qdrant_points), batch_size):
        batch = qdrant_points[i : i + batch_size]
        await vector_store._client.upsert(
            collection_name=ENTITY_COLLECTION,
            points=batch,
        )
        total += len(batch)

    logger.info("entity_indexer_upserted", collection=ENTITY_COLLECTION, points=total)
    return total
