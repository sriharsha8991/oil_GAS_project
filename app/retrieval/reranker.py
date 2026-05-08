"""Tier-weighted reranker — adjusts semantic similarity by document priority.

Per PLAN.md §3.3, each artifact class carries an evidence-tier weight.
Each search result's adjusted score is:

    adjusted_score = raw_score * tier_weight(artifact_class)

The reranker reads ``artifact_class`` from each result dict (attached
upstream by the rag tool from a Postgres lookup, see
``app.db.queries.document_meta``). Results without an attached class
fall back to ``_DEFAULT_WEIGHT``.

Recency boost and scope proximity factors are deferred to Phase 2 when
L3 (hierarchy) and L4 (revisions/timestamps) metadata is enforced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Document priority tiers (PRD §18.2 evidence hierarchy)
_TIER_WEIGHTS: dict[str, float] = {
    # Tier 1 — highest
    "master": 1.0,
    "program": 1.0,
    "learning": 1.0,
    # Tier 2
    "ddr": 0.85,
    "handover": 0.85,
    "risk": 0.85,
    # Tier 3
    "service": 0.75,
    "offset": 0.75,
    # Tier 4
    "action": 0.5,
    # Tier 5
    "media": 0.3,
    "other": 0.5,
}

# Default weight for unknown / unattached artifact classes.
_DEFAULT_WEIGHT = 0.5


@dataclass
class RankedResult:
    """A search result with an adjusted score."""
    text: str
    raw_score: float
    adjusted_score: float
    tier_weight: float
    artifact_class: str = ""
    confidentiality: str = "internal"
    # Tenant scope (echoed from payload)
    user_id: str = ""
    project_id: str = ""
    document_id: str = ""
    # Pass through all original metadata
    doc_name: str = ""
    filename: str = ""
    job_id: str = ""
    page_num: int = 0
    chunk_index: int = 0
    # Chunk-specific
    kind: str = ""
    table_id: str = ""
    table_markdown: str = ""
    # Optional: entity-specific fields
    entity_type: str = ""
    canonical_id: str = ""
    canonical_value: str = ""
    unit: str = ""
    occurrences: int = 0
    mentions: list[dict[str, Any]] | None = None
    # Optional: image-specific fields
    image_type: str = ""
    description: str = ""
    bbox: list[int] | None = None
    # Source collection
    collection: str = ""


def get_tier_weight(artifact_class: str | None) -> float:
    """Look up the tier weight for an artifact class."""
    if not artifact_class:
        return _DEFAULT_WEIGHT
    return _TIER_WEIGHTS.get(artifact_class.lower(), _DEFAULT_WEIGHT)


def rerank(
    results: list[dict[str, Any]],
    *,
    collection: str = "",
) -> list[RankedResult]:
    """Apply per-result tier weights and sort by adjusted score.

    Each ``result`` dict may carry an ``artifact_class`` (attached by the
    caller from a Postgres lookup) and ``confidentiality`` field. Missing
    fields fall back to the default tier weight and ``"internal"``.
    """
    ranked: list[RankedResult] = []
    for r in results:
        raw_score = float(r.get("score", 0.0))
        artifact_class = (r.get("artifact_class") or "").strip().lower()
        tier_weight = get_tier_weight(artifact_class)
        adjusted = raw_score * tier_weight

        ranked.append(RankedResult(
            text=r.get("text", ""),
            raw_score=raw_score,
            adjusted_score=round(adjusted, 4),
            tier_weight=tier_weight,
            artifact_class=artifact_class,
            confidentiality=(r.get("confidentiality") or "internal"),
            user_id=r.get("user_id", "") or "",
            project_id=r.get("project_id", "") or "",
            document_id=r.get("document_id", "") or "",
            doc_name=r.get("doc_name", ""),
            filename=r.get("filename", ""),
            job_id=r.get("job_id", ""),
            page_num=r.get("page_num", 0),
            chunk_index=r.get("chunk_index", 0),
            kind=r.get("kind", "") or "",
            table_id=r.get("table_id", "") or "",
            table_markdown=r.get("table_markdown", "") or "",
            entity_type=r.get("entity_type", ""),
            canonical_id=r.get("canonical_id", ""),
            canonical_value=r.get("canonical_value", ""),
            unit=r.get("unit", "") or "",
            occurrences=int(r.get("occurrences", 0) or 0),
            mentions=r.get("mentions"),
            image_type=r.get("image_type", ""),
            description=r.get("description", ""),
            bbox=r.get("bbox"),
            collection=collection,
        ))

    ranked.sort(key=lambda x: x.adjusted_score, reverse=True)
    return ranked


def merge_and_rerank(
    *result_lists: list[RankedResult],
    limit: int = 10,
) -> list[RankedResult]:
    """Merge multiple ranked result lists and return top-K by adjusted score."""
    all_results: list[RankedResult] = []
    for results in result_lists:
        all_results.extend(results)

    all_results.sort(key=lambda x: x.adjusted_score, reverse=True)
    return all_results[:limit]
