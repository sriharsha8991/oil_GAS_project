"""Post-extraction entity normalization (G-L1.3).

The extractor prompt instructs Gemini to use the controlled vocabulary in
`app.domain.well_taxonomy`, but free-form models still drift. This module
coerces the raw entity list returned by the extractor into the controlled
vocabulary, surfacing every coercion as a `gap` so the per-page graph can
route the page to Pro escalation when drift is significant.

Pure function — no I/O, no model calls.
"""

from __future__ import annotations

from typing import Any

from app.domain.well_taxonomy import (
    DEPTH_TYPES,
    ENTITY_TYPES,
    NPT_CATEGORIES,
    OPERATION_TYPES,
)

# How many out-of-vocab coercions are tolerated before we force escalation.
_MAX_TOLERATED_COERCIONS = 1


def _coerce_lower(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _normalize_one(entity: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Normalize a single entity dict. Returns (normalized, warnings)."""
    warnings: list[str] = []
    out = dict(entity)

    # --- type ---
    raw_type = _coerce_lower(out.get("type"))
    if not raw_type:
        out["type"] = "other"
        warnings.append("entity missing 'type'; coerced to 'other'")
    elif raw_type not in ENTITY_TYPES:
        warnings.append(
            f"entity.type {raw_type!r} is not in the controlled vocabulary; "
            f"coerced to 'other'"
        )
        out["type_raw"] = out.get("type")
        out["type"] = "other"
    else:
        out["type"] = raw_type

    # --- npt_event sub-category lives in 'value' ---
    if out["type"] == "npt_event":
        raw_value = _coerce_lower(out.get("value"))
        if raw_value and raw_value not in NPT_CATEGORIES:
            warnings.append(
                f"npt_event value {raw_value!r} is not an IADC category; "
                f"coerced to 'other'"
            )
            out["value_raw"] = out.get("value")
            out["value"] = "other"
        elif raw_value:
            out["value"] = raw_value

    # --- operation sub-category in 'value' ---
    if out["type"] == "operation":
        raw_value = _coerce_lower(out.get("value"))
        if raw_value and raw_value not in OPERATION_TYPES:
            warnings.append(
                f"operation value {raw_value!r} is not in the controlled "
                f"vocabulary; coerced to 'other'"
            )
            out["value_raw"] = out.get("value")
            out["value"] = "other"
        elif raw_value:
            out["value"] = raw_value

    # --- depth datum/type lives in 'context' or appended to value ---
    # We only validate when the entity carries an explicit `depth_type` key.
    if out["type"] == "depth":
        depth_type = out.get("depth_type")
        if depth_type:
            normalized = str(depth_type).strip().upper()
            if normalized not in DEPTH_TYPES:
                warnings.append(
                    f"depth_type {depth_type!r} is not a known datum; "
                    f"coerced to empty"
                )
                out["depth_type_raw"] = depth_type
                out["depth_type"] = ""
            else:
                out["depth_type"] = normalized

    return out, warnings


def normalize_entities(
    entities: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalize a list of raw extractor entities to the controlled vocabulary.

    Returns (normalized_entities, warnings). Warnings is a flat list of
    human-readable strings — one per coercion. Empty when everything was
    already clean.
    """
    if not entities:
        return [], []

    normalized: list[dict[str, Any]] = []
    all_warnings: list[str] = []
    for raw in entities:
        if not isinstance(raw, dict):
            all_warnings.append(
                f"entity is not an object ({type(raw).__name__}); dropped"
            )
            continue
        norm, warns = _normalize_one(raw)
        normalized.append(norm)
        all_warnings.extend(warns)

    return normalized, all_warnings


def should_force_escalation(warnings: list[str]) -> bool:
    """Return True when the number of coercions justifies routing to Pro."""
    return len(warnings) > _MAX_TOLERATED_COERCIONS
