"""Document outline — high-level shape of a single ingested document.

Used by the synthesizer's ``fetch_document_outline`` tool. Cheap: one
Postgres lookup, one Storage read of ``full_document.md``, two Qdrant
counts. Designed to answer "tell me about this document" without burning
``rag_search`` calls.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import desc, select

from app.db.engine import get_session_factory
from app.db.models import Document, Job
from app.storage.supabase_storage import SupabaseStorage
from app.utils.logging import get_logger
from app.vectorstore.qdrant_store import (
    CHUNKS_COLLECTION_DEFAULT,
    IMAGES_COLLECTION,
    QdrantVectorStore,
)

logger = get_logger(__name__)

MAX_HEADINGS = 40

# Match a markdown heading line: 1–3 leading hashes + space + text.
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)
# Match the per-page boundary the postprocessor writes into full_document.md.
_PAGE_MARKER_RE = re.compile(r"^---\s*\n##\s+Page\s+(\d+)\s*$", re.MULTILINE)


def _parse_headings(full_md: str) -> tuple[list[dict[str, Any]], int]:
    """Return ``(headings, page_count)`` derived from full_document.md.

    Headings are tagged with their nearest preceding page number.
    """
    if not full_md:
        return [], 0

    page_offsets: list[tuple[int, int]] = []  # (offset, page_num)
    for m in _PAGE_MARKER_RE.finditer(full_md):
        page_offsets.append((m.start(), int(m.group(1))))
    page_count = len(page_offsets) or _fallback_page_count(full_md)

    def page_for(offset: int) -> int | None:
        last = None
        for off, pn in page_offsets:
            if off <= offset:
                last = pn
            else:
                break
        return last

    out: list[dict[str, Any]] = []
    for m in _HEADING_RE.finditer(full_md):
        text = m.group(2).strip()
        # Skip the per-page "## Page N" headings themselves.
        if re.match(r"^Page\s+\d+$", text, re.IGNORECASE):
            continue
        out.append({
            "page": page_for(m.start()),
            "level": len(m.group(1)),
            "text": text[:160],
        })
        if len(out) >= MAX_HEADINGS:
            break
    return out, page_count


def _fallback_page_count(full_md: str) -> int:
    """Count `--- ## Page N` markers loosely if the strict regex missed."""
    return len(re.findall(r"^##\s+Page\s+\d+", full_md, re.MULTILINE))


async def fetch_outline(
    *,
    document_id: str,
    user_id: str,
    storage: SupabaseStorage,
    vector_store: QdrantVectorStore,
) -> dict[str, Any] | None:
    """Build the outline payload for one document, owner-scoped.

    Returns ``None`` if the document doesn't exist or isn't owned by the user.
    """
    Session = get_session_factory()
    async with Session() as sess:
        doc_row = (await sess.execute(
            select(
                Document.id,
                Document.filename,
                Document.project_id,
                Document.created_at,
            ).where(
                Document.id == document_id,
                Document.user_id == user_id,
            )
        )).first()
        if doc_row is None:
            return None
        latest_job = (await sess.execute(
            select(Job.pages_total, Job.status, Job.ended_at)
            .where(Job.document_id == document_id, Job.user_id == user_id)
            .order_by(desc(Job.created_at))
            .limit(1)
        )).first()

    full_md = ""
    try:
        full_md = await storage.read_full_document(
            user_id, str(doc_row.project_id), document_id,
        ) or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("outline_full_md_read_failed",
                       document_id=document_id, error=str(exc))

    headings, parsed_pages = _parse_headings(full_md)
    pages_total = (
        int(latest_job.pages_total) if latest_job and latest_job.pages_total
        else parsed_pages
    )

    table_count = await vector_store.count_points(
        collection=CHUNKS_COLLECTION_DEFAULT,
        user_id=user_id,
        document_id=document_id,
        kind="table",
    )
    image_count = await vector_store.count_points(
        collection=IMAGES_COLLECTION,
        user_id=user_id,
        document_id=document_id,
    )

    return {
        "document_id": document_id,
        "filename": doc_row.filename,
        "page_count": pages_total or None,
        "table_count": table_count,
        "image_count": image_count,
        "headings": headings,
        "ingested_at": (
            latest_job.ended_at.isoformat()
            if latest_job and latest_job.ended_at
            else doc_row.created_at.isoformat()
        ),
        "ingest_status": latest_job.status if latest_job else None,
    }
