"""Tier-weighted reranker — adjusts semantic similarity by document priority.

Per PLAN.md §3.3, different artifact classes carry different trustworthiness
weights. The reranker multiplies the raw semantic score by:
  tier_weight × recency_boost × scope_proximity

Phase 1: Only tier_weight is implemented (artifact class → weight).
Recency and scope proximity are deferred to Phase 2 when L3/L4 metadata
is available.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Document priority tiers (PRD §18.2 evidence hierarchy)
_TIER_WEIGHTS: dict[str, float] = {
    # Tier 1 — highest
    "master": 1.0,
    "program": 1.0,
    # Tier 2
    "ddr": 0.85,
    "handover": 0.85,
    "risk": 0.85,
    # Tier 3
    "service": 0.75,
    # Tier 4
    "offset": 0.5,
    "learning": 0.5,
    "action": 0.5,
    # Tier 5
    "media": 0.3,
}

# Default weight for unknown artifact classes
_DEFAULT_WEIGHT = 0.7


@dataclass
class RankedResult:
    """A search result with an adjusted score."""
    text: str
    raw_score: float
    adjusted_score: float
    tier_weight: float
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
    artifact_class: str | None = None,
    collection: str = "",
) -> list[RankedResult]:
    """Rerank a list of search results by applying tier weights.

    Args:
        results: Raw search results from Qdrant (list of dicts with
            'text', 'score', and metadata fields).
        artifact_class: If known, the artifact class to use for all
            results in this batch. If None, uses default weight.
        collection: Source collection name (for provenance).

    Returns:
        Sorted list of RankedResult (highest adjusted_score first).
    """
    tier_weight = get_tier_weight(artifact_class)

    ranked: list[RankedResult] = []
    for r in results:
        raw_score = float(r.get("score", 0.0))
        adjusted = raw_score * tier_weight

        ranked.append(RankedResult(
            text=r.get("text", ""),
            raw_score=raw_score,
            adjusted_score=round(adjusted, 4),
            tier_weight=tier_weight,
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
    """Merge multiple ranked result lists and return top-K by adjusted score.

    Used by the hybrid pipeline to combine results from multiple
    collections (chunks, entities, images) into a single ranked list.
    """
    all_results: list[RankedResult] = []
    for results in result_lists:
        all_results.extend(results)

    all_results.sort(key=lambda x: x.adjusted_score, reverse=True)
    return all_results[:limit]
