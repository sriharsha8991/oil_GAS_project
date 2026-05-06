"""Image description indexer — embeds and upserts image descriptions into Qdrant.

Each described image/figure/log strip from the extraction pipeline gets
one point in the ``image_descriptions`` collection. Enables image-grounding
queries: "show me the wellbore diagram" → retrieves the relevant page + bbox.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.utils.logging import get_logger

if TYPE_CHECKING:
    from app.vectorstore.embedder import GeminiEmbedder
    from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)

IMAGE_COLLECTION = "wellsynthai_images"


@dataclass
class ImagePoint:
    """A single image description ready for Qdrant upsert."""
    text: str               # embedding source (description)
    user_id: str
    role: str
    organization: str | None
    project_id: str
    document_id: str
    image_id: str
    description: str
    image_type: str          # log_strip | schematic | plot | photo | table_image | other
    location: str
    doc_name: str
    job_id: str
    filename: str
    page_num: int
    bbox: list[int] | None = None


def build_image_points(
    page_map: dict[int, dict],
    *,
    user_id: str,
    role: str,
    organization: str | None,
    project_id: str,
    document_id: str,
    doc_name: str,
    job_id: str,
    filename: str,
) -> list[ImagePoint]:
    """Extract image descriptions from page_map into ImagePoint objects.

    Skips low-signal images (very short or empty descriptions). Embedding
    text is built to be retrieval-friendly: image_type + description plus
    the surrounding section heading when available.
    """
    points: list[ImagePoint] = []
    for page_num in sorted(page_map.keys()):
        page = page_map[page_num] or {}
        section_hint = _nearest_heading(page.get("markdown", "") or "")
        for img in page.get("images", []) or []:
            desc = (img.get("description") or "").strip()
            if len(desc) < 15:
                # Drop logos / decorative items the prompt should already
                # filter, but be defensive in case the model slips one in.
                continue
            img_type = img.get("image_type", "other") or "other"
            location = img.get("location", "") or ""

            # Retrieval-friendly embedding text.
            text_bits = [
                f"{img_type} on page {page_num + 1}",
                desc,
            ]
            if location:
                text_bits.append(f"Location: {location}")
            if section_hint:
                text_bits.append(f"Section: {section_hint}")
            text = ". ".join(text_bits)

            points.append(ImagePoint(
                text=text,
                user_id=user_id,
                role=role,
                organization=organization,
                project_id=project_id,
                document_id=document_id,
                image_id=str(img.get("id") or f"img_{page_num}_{len(points)}"),
                description=desc,
                image_type=img_type,
                location=location,
                doc_name=doc_name,
                job_id=job_id,
                filename=filename,
                page_num=page_num,
                bbox=img.get("bbox"),
            ))
    return points


def _nearest_heading(markdown: str) -> str:
    """Return the first H1/H2/H3 from the page markdown (best-effort context)."""
    if not markdown:
        return ""
    for line in markdown.splitlines():
        s = line.strip()
        if s.startswith(("# ", "## ", "### ")):
            return s.lstrip("# ").strip()[:120]
    return ""


async def upsert_image_points(
    points: list[ImagePoint],
    embedder: "GeminiEmbedder",
    vector_store: "QdrantVectorStore",
) -> int:
    """Embed and upsert image description points into Qdrant.

    Returns the number of points upserted.
    """
    if not points:
        return 0

    texts = [p.text for p in points]
    embeddings = await embedder.embed_texts(texts, task_type="RETRIEVAL_DOCUMENT")

    from qdrant_client import models

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
                "image_id": point.image_id,
                "description": point.description,
                "image_type": point.image_type,
                "location": point.location,
                "doc_name": point.doc_name,
                "job_id": point.job_id,
                "filename": point.filename,
                "page_num": point.page_num,
                "bbox": point.bbox,
            },
        )
        for point, embedding in zip(points, embeddings)
    ]

    batch_size = 100
    total = 0
    for i in range(0, len(qdrant_points), batch_size):
        batch = qdrant_points[i : i + batch_size]
        await vector_store._client.upsert(
            collection_name=IMAGE_COLLECTION,
            points=batch,
        )
        total += len(batch)

    logger.info("image_indexer_upserted", collection=IMAGE_COLLECTION, points=total)
    return total
