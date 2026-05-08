"""Evidence card builder — converts retrieved hits into UI-renderable cards."""

from __future__ import annotations

from typing import Any

from app.agents.rag_tools import SearchResult


def source_dict(r: SearchResult, source_id: str) -> dict[str, Any]:
    """Serialize a SearchResult to the wire-format `sources` dict."""
    return {
        "source_id": source_id,
        "text": r.text,
        "score": r.score,
        "user_id": r.user_id,
        "project_id": r.project_id,
        "document_id": r.document_id,
        "doc_name": r.doc_name,
        "filename": r.filename,
        "job_id": r.job_id,
        "page_num": r.page_num,
        "chunk_index": r.chunk_index,
        "kind": r.kind,
        "table_id": r.table_id,
        "table_markdown": r.table_markdown,
        "entity_type": r.entity_type,
        "canonical_id": r.canonical_id,
        "canonical_value": r.canonical_value,
        "unit": r.unit,
        "occurrences": r.occurrences,
        "image_type": r.image_type,
        "description": r.description,
        "bbox": r.bbox,
        "collection": r.collection,
    }


def build_sources(results: list[SearchResult]) -> list[dict[str, Any]]:
    """Build the source list with stable Sn ids the synthesizer can cite."""
    sources: list[dict[str, Any]] = []
    for idx, r in enumerate(results, start=1):
        sources.append(source_dict(r, f"S{idx}"))
    return sources


def build_evidence(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build UI-friendly evidence cards from the source list."""
    evidence: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    for s in sources:
        document_id = s.get("document_id")
        page_num = s.get("page_num")
        doc_name = s.get("doc_name")
        if page_num is None:
            continue

        if s.get("kind") == "table" and s.get("table_markdown"):
            key = ("table", doc_name, page_num, s.get("table_id"))
            if key in seen:
                continue
            seen.add(key)
            evidence.append({
                "kind": "table",
                "source_id": s.get("source_id"),
                "doc_name": doc_name,
                "page_num": page_num,
                "table_id": s.get("table_id"),
                "table_markdown": s.get("table_markdown"),
                "url": (
                    f"/documents/{document_id}/pages/{page_num}/image"
                    if document_id else None
                ),
            })
            continue

        if s.get("collection") == "wellsynthai_images":
            bbox = s.get("bbox")
            if bbox and len(bbox) == 4 and document_id:
                ymin, xmin, ymax, xmax = bbox
                key = ("crop", document_id, page_num, ymin, xmin, ymax, xmax)
                if key in seen:
                    continue
                seen.add(key)
                evidence.append({
                    "kind": "bbox_crop",
                    "source_id": s.get("source_id"),
                    "doc_name": doc_name,
                    "page_num": page_num,
                    "image_type": s.get("image_type"),
                    "description": s.get("description"),
                    "bbox": bbox,
                    "url": (
                        f"/documents/{document_id}/pages/{page_num}/image/crop"
                        f"?ymin={ymin}&xmin={xmin}&ymax={ymax}&xmax={xmax}"
                    ),
                })
            elif document_id:
                key = ("page", document_id, page_num)
                if key in seen:
                    continue
                seen.add(key)
                evidence.append({
                    "kind": "page_image",
                    "source_id": s.get("source_id"),
                    "doc_name": doc_name,
                    "page_num": page_num,
                    "image_type": s.get("image_type"),
                    "description": s.get("description"),
                    "url": f"/documents/{document_id}/pages/{page_num}/image",
                })

    return evidence
