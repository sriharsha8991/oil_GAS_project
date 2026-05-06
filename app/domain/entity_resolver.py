"""Entity resolution — deduplicates extracted entities against a canonical store.

Phase 1B: Uses string similarity (normalized Levenshtein) + domain rules
to decide merge-vs-create. Vector similarity (embedding cosine) is added
in Phase 2 when the canonical store moves to Qdrant.

The resolver is stateless per invocation — it receives the current
canonical store (a list) and returns updated entities with canonical_ids.

Usage:
    from app.domain.entity_resolver import EntityResolver
    resolver = EntityResolver()
    resolved = resolver.resolve_entities(raw_entities, canonical_store)
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from app.domain.formations import FormationRegistry
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Thresholds for string-similarity merge (per PLAN.md §L2)
_STRING_SIM_THRESHOLD = 0.85
_STRICT_SIM_THRESHOLD = 0.92  # for high-confidence auto-merge


@dataclass
class CanonicalEntity:
    """A canonical (deduplicated) entity in the store."""
    canonical_id: str
    entity_type: str
    canonical_value: str
    unit: str = ""
    aliases: list[str] = field(default_factory=list)
    first_seen_doc: str = ""
    first_seen_page: int = -1
    occurrences: int = 1


@dataclass
class ResolvedEntity:
    """An extracted entity with resolution metadata attached."""
    type: str
    value: str
    unit: str = ""
    context: str = ""
    bbox: list[int] | None = None
    # Resolution fields
    canonical_id: str = ""
    canonical_value: str = ""
    resolution: str = ""  # "matched" | "created" | "normalized"
    similarity: float = 0.0


def _normalize_text(text: str) -> str:
    """Lowercase, collapse whitespace, strip common punctuation."""
    t = text.lower().strip()
    t = re.sub(r"[_\-/\\]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t


def _string_similarity(a: str, b: str) -> float:
    """Normalized string similarity using SequenceMatcher (0.0 to 1.0)."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, _normalize_text(a), _normalize_text(b)).ratio()


class EntityResolver:
    """Resolve raw extracted entities against a canonical store.

    The resolver applies these rules in order:
    1. **Exact match** (normalized) → merge immediately
    2. **Domain-specific normalization** (formations via FormationRegistry,
       NPT via enum matching) → normalize + merge
    3. **Fuzzy string match** (similarity ≥ threshold) → merge
    4. **No match** → create new canonical entry
    """

    def __init__(
        self,
        formation_registry: FormationRegistry | None = None,
        string_threshold: float = _STRING_SIM_THRESHOLD,
        strict_threshold: float = _STRICT_SIM_THRESHOLD,
    ) -> None:
        self._formations = formation_registry or FormationRegistry()
        self._string_threshold = string_threshold
        self._strict_threshold = strict_threshold

    def resolve_entities(
        self,
        raw_entities: list[dict[str, Any]],
        canonical_store: list[CanonicalEntity],
        *,
        doc_name: str = "",
        page_num: int = -1,
    ) -> tuple[list[ResolvedEntity], list[CanonicalEntity]]:
        """Resolve a batch of raw entities.

        Args:
            raw_entities: List of entity dicts from the extractor
                (keys: type, value, unit, context, bbox).
            canonical_store: Current canonical entities (mutated in place
                when new entries are created).
            doc_name: Source document name (for provenance).
            page_num: Source page number (for provenance).

        Returns:
            (resolved_entities, updated_canonical_store) tuple.
        """
        resolved: list[ResolvedEntity] = []

        for raw in raw_entities:
            etype = raw.get("type", "other") or "other"
            value = raw.get("value", "") or ""
            unit = raw.get("unit", "") or ""
            context = raw.get("context", "") or ""
            bbox = raw.get("bbox")

            if not value.strip():
                continue

            entity = ResolvedEntity(
                type=etype,
                value=value,
                unit=unit,
                context=context,
                bbox=bbox,
            )

            # Step 1: Domain-specific normalization
            normalized_value = self._domain_normalize(etype, value)
            if normalized_value != value:
                entity.canonical_value = normalized_value
                entity.resolution = "normalized"

            # Step 2: Search canonical store for match
            match, sim = self._find_match(
                etype, normalized_value or value, canonical_store
            )

            if match is not None:
                # Merge with existing canonical
                entity.canonical_id = match.canonical_id
                entity.canonical_value = match.canonical_value
                entity.similarity = sim
                entity.resolution = "matched"
                match.occurrences += 1
                # Add as alias if it's a new variant
                norm_val = _normalize_text(value)
                if norm_val not in [_normalize_text(a) for a in match.aliases]:
                    match.aliases.append(value)
            else:
                # Create new canonical entry
                new_canonical = CanonicalEntity(
                    canonical_id=str(uuid.uuid4()),
                    entity_type=etype,
                    canonical_value=normalized_value or value,
                    unit=unit,
                    aliases=[value],
                    first_seen_doc=doc_name,
                    first_seen_page=page_num,
                )
                canonical_store.append(new_canonical)
                entity.canonical_id = new_canonical.canonical_id
                entity.canonical_value = new_canonical.canonical_value
                entity.resolution = "created"
                entity.similarity = 1.0

            resolved.append(entity)

        return resolved, canonical_store

    def _domain_normalize(self, entity_type: str, value: str) -> str:
        """Apply domain-specific normalization rules."""
        if entity_type == "formation":
            return self._formations.resolve(value)

        if entity_type == "npt_event":
            return self._normalize_npt(value)

        return value

    def _normalize_npt(self, value: str) -> str:
        """Map free-text NPT descriptions to canonical IADC categories."""
        norm = _normalize_text(value)

        # Direct keyword mapping
        _NPT_KEYWORDS: dict[str, str] = {
            "stuck pipe": "stuck_pipe",
            "stuck": "stuck_pipe",
            "differential sticking": "stuck_pipe",
            "lost circulation": "lost_circulation",
            "lost returns": "lost_circulation",
            "losses": "lost_circulation",
            "well control": "well_control",
            "kick": "well_control",
            "wait on weather": "wait_on_weather",
            "wow": "wait_on_weather",
            "weather delay": "wait_on_weather",
            "wait on cement": "wait_on_cement",
            "woc": "wait_on_cement",
            "wait on equipment": "wait_on_equipment",
            "woe": "wait_on_equipment",
            "wait on orders": "wait_on_orders",
            "fishing": "fishing",
            "fish": "fishing",
            "twist off": "twist_off",
            "twistoff": "twist_off",
            "reaming": "reaming",
            "ream": "reaming",
            "bha failure": "bha_failure",
            "downhole tool failure": "bha_failure",
            "mwd failure": "mwd_lwd_failure",
            "lwd failure": "mwd_lwd_failure",
            "mwd lwd failure": "mwd_lwd_failure",
            "directional": "directional_issues",
            "directional issues": "directional_issues",
            "hole condition": "hole_condition",
            "tight hole": "hole_condition",
            "hole cleaning": "hole_condition",
            "mechanical failure": "mechanical_failure",
            "equipment failure": "mechanical_failure",
            "rig breakdown": "mechanical_failure",
        }

        for keyword, canonical in _NPT_KEYWORDS.items():
            if keyword in norm:
                return canonical

        return value  # no match → return as-is

    def _find_match(
        self,
        entity_type: str,
        value: str,
        store: list[CanonicalEntity],
    ) -> tuple[CanonicalEntity | None, float]:
        """Find the best matching canonical entity.

        Returns (match, similarity) or (None, 0.0).
        """
        best_match: CanonicalEntity | None = None
        best_sim = 0.0
        norm_value = _normalize_text(value)

        for canonical in store:
            if canonical.entity_type != entity_type:
                continue

            # Exact normalized match
            if _normalize_text(canonical.canonical_value) == norm_value:
                return canonical, 1.0

            # Check aliases
            for alias in canonical.aliases:
                if _normalize_text(alias) == norm_value:
                    return canonical, 1.0

            # Fuzzy match
            sim = _string_similarity(value, canonical.canonical_value)
            if sim > best_sim:
                best_sim = sim
                best_match = canonical

            # Also check aliases for fuzzy
            for alias in canonical.aliases:
                alias_sim = _string_similarity(value, alias)
                if alias_sim > best_sim:
                    best_sim = alias_sim
                    best_match = canonical

        if best_match is not None and best_sim >= self._string_threshold:
            return best_match, best_sim

        return None, 0.0
