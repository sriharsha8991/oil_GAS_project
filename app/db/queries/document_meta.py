"""Document governance metadata loader for the retrieval layer.

The retrieval layer (reranker + tool wrappers in ``app.agents.rag_tools``)
needs to know each result document's ``artifact_class``,
``confidentiality``, ``revision``, and whether it has been superseded.

Rather than denormalize this into every Qdrant payload (which would
require a backfill on every artifact_class edit), we look it up from
Postgres at query time. One bounded query per search call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document


@dataclass(frozen=True)
class DocumentMeta:
    artifact_class: str
    confidentiality: str
    revision: int
    is_superseded: bool


async def fetch_document_meta(
    sess: AsyncSession,
    document_ids: Iterable[str],
) -> dict[str, DocumentMeta]:
    """Return ``{document_id: DocumentMeta}`` for the given ids.

    Unknown ids are simply absent from the result. Caller is responsible
    for treating absent ids as ungoverned (default tier weight, default
    confidentiality, not superseded).
    """
    ids: list[UUID] = []
    for raw in document_ids:
        if raw is None:
            continue
        try:
            ids.append(UUID(str(raw)))
        except (ValueError, AttributeError):
            continue
    if not ids:
        return {}

    rows = (await sess.execute(
        select(
            Document.id,
            Document.artifact_class,
            Document.confidentiality,
            Document.revision,
            Document.superseded_by,
        ).where(Document.id.in_(ids))
    )).all()

    out: dict[str, DocumentMeta] = {}
    for r in rows:
        out[str(r.id)] = DocumentMeta(
            artifact_class=r.artifact_class or "other",
            confidentiality=r.confidentiality or "internal",
            revision=int(r.revision or 1),
            is_superseded=r.superseded_by is not None,
        )
    return out
