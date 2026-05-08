"""Documents route — list, view, delete, and serve per-document artifacts.

Endpoints
---------
- ``GET    /documents``                                  list user's documents
- ``GET    /documents/{document_id}``                    document metadata + latest job
- ``DELETE /documents/{document_id}``                    delete + cascade
- ``GET    /documents/{document_id}/full``               full markdown
- ``GET    /documents/{document_id}/pages/{n}/image``    redirect to signed URL
- ``GET    /documents/{document_id}/pages/{n}/image/crop`` bbox crop
"""

from __future__ import annotations

import asyncio
from typing import Any

import fitz  # PyMuPDF
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, select, update

from app.auth.deps import AuthUser, current_user
from app.config import get_settings
from app.db.engine import get_session_factory
from app.db.models import Document, Job
from app.domain.well_taxonomy import ARTIFACT_CLASSES, CONFIDENTIALITY_CLASSES
from app.storage.supabase_storage import SupabaseStorage
from app.utils.logging import get_logger
from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


async def _resolve_doc(document_id: str, user: AuthUser) -> Document:
    Session = get_session_factory()
    async with Session() as sess:
        row = (await sess.execute(
            select(Document).where(Document.id == document_id)
        )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="document not found")
    if str(row.user_id) != user.id and user.role != "super_admin":
        raise HTTPException(status_code=403, detail="not authorized for this document")
    return row


def _doc_to_dict(d: Document, latest_job: Job | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(d.id),
        "user_id": str(d.user_id),
        "project_id": str(d.project_id),
        "filename": d.filename,
        "artifact_class": d.artifact_class,
        "revision": d.revision,
        "superseded_by": str(d.superseded_by) if d.superseded_by else None,
        "confidentiality": d.confidentiality,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }
    if latest_job is not None:
        out["latest_job"] = {
            "job_id": str(latest_job.id),
            "status": latest_job.status,
            "current_phase": latest_job.current_phase,
            "pages_total": latest_job.pages_total or 0,
            "pages_done": latest_job.pages_done or 0,
            "error": latest_job.error,
        }
    return out


@router.get(
    "",
    summary="List documents (inlines latest_job per row)",
)
async def list_documents(
    user: AuthUser = Depends(current_user),
    project_id: str | None = Query(None),
    scope: str = Query(
        "mine",
        regex="^(mine|all)$",
        description="'mine' (default) returns the caller's documents. "
                    "'all' returns every document and requires super_admin.",
    ),
) -> dict[str, Any]:
    if scope == "all" and user.role != "super_admin":
        raise HTTPException(status_code=403, detail="requires_super_admin")

    Session = get_session_factory()
    async with Session() as sess:
        stmt = select(Document)
        if scope == "mine":
            stmt = stmt.where(Document.user_id == user.id)
        if project_id:
            stmt = stmt.where(Document.project_id == project_id)
        stmt = stmt.order_by(desc(Document.created_at))
        rows = list((await sess.execute(stmt)).scalars().all())

        # Inline the latest job per doc in a single bounded query (B-048).
        latest_by_doc: dict[str, Job] = {}
        if rows:
            doc_ids = [r.id for r in rows]
            jobs = (await sess.execute(
                select(Job)
                .where(Job.document_id.in_(doc_ids))
                .order_by(Job.document_id, desc(Job.created_at))
            )).scalars().all()
            for j in jobs:
                key = str(j.document_id)
                if key not in latest_by_doc:
                    latest_by_doc[key] = j

    return {
        "count": len(rows),
        "scope": scope,
        "documents": [
            _doc_to_dict(r, latest_by_doc.get(str(r.id))) for r in rows
        ],
    }


@router.get("/{document_id}", summary="Document detail")
async def get_document(
    document_id: str, user: AuthUser = Depends(current_user)
) -> dict[str, Any]:
    d = await _resolve_doc(document_id, user)
    Session = get_session_factory()
    async with Session() as sess:
        latest = (await sess.execute(
            select(Job).where(Job.document_id == document_id)
            .order_by(desc(Job.created_at)).limit(1)
        )).scalar_one_or_none()
    return _doc_to_dict(d, latest)


@router.delete("/{document_id}", summary="Delete document + cascade")
async def delete_document(
    document_id: str,
    request: Request,
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    d = await _resolve_doc(document_id, user)

    settings = get_settings()
    storage = SupabaseStorage(settings)
    vector_store: QdrantVectorStore | None = getattr(
        request.app.state, "qdrant", None
    )

    await storage.delete_document(str(d.user_id), str(d.project_id), str(d.id))
    if vector_store is not None:
        await vector_store.delete_by_document(str(d.id))

    Session = get_session_factory()
    async with Session() as sess:
        await sess.execute(
            delete(Document).where(Document.id == document_id)
        )
        await sess.commit()

    logger.info("document_deleted", user_id=user.id, document_id=document_id)
    return {"deleted": document_id}


# ---------------------------------------------------------------------------
# PATCH /documents/{id} — governance metadata (artifact_class, etc.)
# ---------------------------------------------------------------------------


class DocumentPatch(BaseModel):
    artifact_class: str | None = Field(default=None)
    confidentiality: str | None = Field(default=None)
    superseded_by: str | None = Field(
        default=None,
        description=(
            "Document id of the newer revision that supersedes this one. "
            "Pass null to clear. The superseding document must belong to "
            "the same user."
        ),
    )


@router.patch(
    "/{document_id}",
    summary="Update governance metadata (artifact_class, confidentiality, supersession)",
)
async def patch_document(
    document_id: str,
    patch: DocumentPatch,
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    d = await _resolve_doc(document_id, user)

    updates: dict[str, Any] = {}
    if patch.artifact_class is not None:
        norm = patch.artifact_class.strip().lower()
        if norm and norm not in ARTIFACT_CLASSES:
            raise HTTPException(
                status_code=400,
                detail=f"invalid artifact_class; allowed: {sorted(ARTIFACT_CLASSES)}",
            )
        updates["artifact_class"] = norm or "other"

    if patch.confidentiality is not None:
        norm = patch.confidentiality.strip().lower()
        if norm and norm not in CONFIDENTIALITY_CLASSES:
            raise HTTPException(
                status_code=400,
                detail=(
                    "invalid confidentiality; allowed: "
                    f"{sorted(CONFIDENTIALITY_CLASSES)}"
                ),
            )
        updates["confidentiality"] = norm or "internal"

    if "superseded_by" in patch.model_fields_set:
        if patch.superseded_by in (None, ""):
            updates["superseded_by"] = None
        else:
            if patch.superseded_by == document_id:
                raise HTTPException(
                    status_code=400, detail="document cannot supersede itself"
                )
            target = await _resolve_doc(patch.superseded_by, user)
            updates["superseded_by"] = target.id

    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")

    Session = get_session_factory()
    async with Session() as sess:
        await sess.execute(
            update(Document).where(Document.id == document_id).values(**updates)
        )
        await sess.commit()

        refreshed = (await sess.execute(
            select(Document).where(Document.id == document_id)
        )).scalar_one()

    logger.info(
        "document_patched",
        user_id=user.id,
        document_id=document_id,
        fields=list(updates.keys()),
    )
    return _doc_to_dict(refreshed)


# ---------------------------------------------------------------------------
# POST /documents/{id}/auto-classify — re-trigger LLM classifier
# ---------------------------------------------------------------------------


@router.post(
    "/{document_id}/auto-classify",
    summary="Re-run the LLM artifact-class classifier on this document",
)
async def auto_classify(
    document_id: str,
    force: bool = Query(
        False,
        description=(
            "When false (default), only re-classifies if the current class is 'other'. "
            "Set true to overwrite a non-default class."
        ),
    ),
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    from sqlalchemy import update as _update

    from app.db.queries.document_outline import _parse_headings
    from app.pipeline.artifact_classifier import classify_artifact

    d = await _resolve_doc(document_id, user)

    if not force and (d.artifact_class or "").lower() != "other":
        return {
            "document_id": document_id,
            "artifact_class": d.artifact_class,
            "skipped": True,
            "reason": "artifact_class already set; pass force=true to overwrite",
        }

    settings = get_settings()
    storage = SupabaseStorage(settings)

    full_md = await storage.read_full_document(
        str(d.user_id), str(d.project_id), str(d.id)
    ) or ""
    if not full_md:
        raise HTTPException(
            status_code=409, detail="full_document.md not available; ingest may still be running"
        )

    headings, page_count = _parse_headings(full_md)
    cls, confidence = await classify_artifact(
        filename=d.filename,
        headings=headings,
        page_count=page_count or None,
        table_count=0,
        image_count=0,
        content_sample=full_md[:600],
        settings=settings,
    )

    if cls == "other":
        return {
            "document_id": document_id,
            "artifact_class": d.artifact_class,
            "suggested": "other",
            "confidence": confidence,
            "applied": False,
            "reason": "classifier was unsure; left current class in place",
        }

    Session = get_session_factory()
    async with Session() as sess:
        await sess.execute(
            _update(Document)
            .where(Document.id == document_id)
            .values(artifact_class=cls)
        )
        await sess.commit()

    logger.info(
        "document_auto_classified",
        user_id=user.id,
        document_id=document_id,
        artifact_class=cls,
        confidence=confidence,
        forced=force,
    )
    return {
        "document_id": document_id,
        "artifact_class": cls,
        "previous_class": d.artifact_class,
        "confidence": confidence,
        "applied": True,
    }


@router.get(
    "/{document_id}/full",
    response_class=PlainTextResponse,
    summary="Combined full-document markdown",
)
async def get_full_document(
    document_id: str, user: AuthUser = Depends(current_user)
) -> str:
    d = await _resolve_doc(document_id, user)
    settings = get_settings()
    storage = SupabaseStorage(settings)
    content = await storage.read_full_document(
        str(d.user_id), str(d.project_id), str(d.id)
    )
    if content is None:
        raise HTTPException(status_code=404, detail="full_document.md not found")
    return content


@router.get(
    "/{document_id}/pages/{page_num}/image",
    summary="Rendered page PNG (302 to a short-TTL signed URL)",
)
async def get_page_image(
    document_id: str, page_num: int, user: AuthUser = Depends(current_user)
) -> RedirectResponse:
    d = await _resolve_doc(document_id, user)
    settings = get_settings()
    storage = SupabaseStorage(settings)
    try:
        url = await asyncio.to_thread(
            storage.signed_url_page_png,
            str(d.user_id), str(d.project_id), str(d.id), page_num,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=404, detail=f"Page PNG not available: {exc}"
        )
    return RedirectResponse(url=url, status_code=302)


@router.get(
    "/{document_id}/pages/{page_num}/image/crop",
    summary="Crop a bbox region from the rendered page PNG",
)
async def get_page_crop(
    document_id: str,
    page_num: int,
    ymin: int = Query(..., ge=0, le=1000),
    xmin: int = Query(..., ge=0, le=1000),
    ymax: int = Query(..., ge=0, le=1000),
    xmax: int = Query(..., ge=0, le=1000),
    user: AuthUser = Depends(current_user),
) -> Response:
    if ymax <= ymin or xmax <= xmin:
        raise HTTPException(
            status_code=400, detail="Invalid bbox: ymax>ymin and xmax>xmin required"
        )
    d = await _resolve_doc(document_id, user)
    settings = get_settings()
    storage = SupabaseStorage(settings)
    png_bytes = await storage.read_page_png(
        str(d.user_id), str(d.project_id), str(d.id), page_num
    )
    if png_bytes is None:
        raise HTTPException(status_code=404, detail="Page PNG not persisted")

    def _crop() -> bytes:
        pix = fitz.Pixmap(png_bytes)
        w, h = pix.width, pix.height
        x0 = int(xmin / 1000 * w)
        y0 = int(ymin / 1000 * h)
        x1 = int(xmax / 1000 * w)
        y1 = int(ymax / 1000 * h)
        clip = fitz.IRect(x0, y0, x1, y1)
        cropped = fitz.Pixmap(pix, clip)
        return cropped.tobytes("png")

    png = await asyncio.to_thread(_crop)
    return Response(content=png, media_type="image/png")
