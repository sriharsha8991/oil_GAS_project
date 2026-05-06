"""Well-domain taxonomy — open vocabularies with common examples.

Each category is an open set of commonly-seen values.  They are NOT
closed enums because real-world well data has far too many variations.
The extractor is free to output values outside these lists — the resolver
and downstream code treat these as *guidance*, not constraints.

Sources:
  - IADC NPT code list (common subset)
  - Standard drilling operation classifications
  - AAPG / SPE depth datum conventions
  - ISO unit codes for oilfield quantities
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# NPT (Non-Productive Time) categories — common IADC-aligned examples
# ---------------------------------------------------------------------------

NPT_CATEGORIES: set[str] = {
    "mechanical_failure",
    "stuck_pipe",
    "lost_circulation",
    "well_control",
    "wait_on_weather",
    "wait_on_cement",
    "wait_on_equipment",
    "wait_on_orders",
    "fishing",
    "reaming",
    "twist_off",
    "bha_failure",
    "mwd_lwd_failure",
    "directional_issues",
    "hole_condition",
    "other",
}


# ---------------------------------------------------------------------------
# Operation types — common examples
# ---------------------------------------------------------------------------

OPERATION_TYPES: set[str] = {
    "drilling",
    "tripping",
    "casing",
    "cementing",
    "logging",
    "completion",
    "workover",
    "circulation",
    "bop_test",
    "survey",
    "rig_up",
    "rig_down",
    "running_liner",
    "perforation",
    "stimulation",
    "other",
}


# ---------------------------------------------------------------------------
# Depth types (datum reference) — common examples
# ---------------------------------------------------------------------------

DEPTH_TYPES: set[str] = {
    "MD",       # Measured Depth
    "TVD",      # True Vertical Depth
    "KB",       # Kelly Bushing
    "RT",       # Rotary Table
    "MSL",      # Mean Sea Level
    "AHBDF",    # Along-Hole Below Drill Floor
    "RKB",      # Rotary Kelly Bushing
    "BRT",      # Below Rotary Table
    "SS",       # Sub-Sea
}


# ---------------------------------------------------------------------------
# Section types — common examples
# ---------------------------------------------------------------------------

SECTION_TYPES: set[str] = {
    "conductor",
    "surface",
    "intermediate",
    "production",
    "completion",
    "liner",
    "open_hole",
}


# ---------------------------------------------------------------------------
# Entity types (what the extractor can tag) — common examples
# ---------------------------------------------------------------------------

ENTITY_TYPES: set[str] = {
    "well_id",
    "depth",
    "formation",
    "npt_event",
    "fluid",
    "bha",
    "date",
    "operation",
    "pressure",
    "temperature",
    "casing",
    "bit",
    "mud_weight",
    "rig",
    "personnel",
    "equipment",
    "other",
}


# ---------------------------------------------------------------------------
# Unit examples by measurement category
# ---------------------------------------------------------------------------

LENGTH_UNITS: set[str] = {"ft", "m", "in", "mm", "cm"}
PRESSURE_UNITS: set[str] = {"psi", "kPa", "MPa", "bar", "atm"}
DENSITY_UNITS: set[str] = {"ppg", "kg/m3", "sg", "lb/ft3"}
VOLUME_UNITS: set[str] = {"bbl", "m3", "gal", "L"}
TEMPERATURE_UNITS: set[str] = {"F", "C"}
TIME_UNITS: set[str] = {"hours", "minutes", "days"}


# ---------------------------------------------------------------------------
# Artifact classes (PRD §10) — common examples
# ---------------------------------------------------------------------------

ARTIFACT_CLASSES: set[str] = {
    "master",       # Approved drilling program, AFE
    "program",      # Well program, engineering design
    "offset",       # Offset well data, analog reports
    "ddr",          # Daily Drilling Report
    "handover",     # Section or well handover note
    "service",      # Service company reports (mud, DD, MWD)
    "risk",         # Risk register, hazard assessment
    "action",       # Action items / punch list
    "learning",     # Lessons learned
    "media",        # Photos, videos, screenshots
}


# ---------------------------------------------------------------------------
# Export utilities
# ---------------------------------------------------------------------------


def taxonomy_as_dict() -> dict[str, list[str]]:
    """Export the full taxonomy as a JSON-serializable dict.

    Used by the extractor prompt builder to inject common values as
    guidance for Gemini — not as strict constraints.
    """
    return {
        "npt_categories": sorted(NPT_CATEGORIES),
        "operation_types": sorted(OPERATION_TYPES),
        "depth_types": sorted(DEPTH_TYPES),
        "section_types": sorted(SECTION_TYPES),
        "entity_types": sorted(ENTITY_TYPES),
        "length_units": sorted(LENGTH_UNITS),
        "pressure_units": sorted(PRESSURE_UNITS),
        "density_units": sorted(DENSITY_UNITS),
        "volume_units": sorted(VOLUME_UNITS),
        "temperature_units": sorted(TEMPERATURE_UNITS),
        "time_units": sorted(TIME_UNITS),
        "artifact_classes": sorted(ARTIFACT_CLASSES),
    }


def entity_type_hints() -> str:
    """Build a compact hint string listing common entity types + example values.

    Injected into the extractor prompt as guidance. The model may output
    values outside these lists when appropriate.
    """
    lines = [
        "Common entity types and example values (not exhaustive — use other values when appropriate):",
        f"  type (common): {', '.join(sorted(ENTITY_TYPES))}",
        f"  NPT categories (for npt_event): {', '.join(sorted(NPT_CATEGORIES))}",
        f"  operation types (for operation): {', '.join(sorted(OPERATION_TYPES))}",
        f"  depth types (for depth): {', '.join(sorted(DEPTH_TYPES))}",
        f"  section types (context): {', '.join(sorted(SECTION_TYPES))}",
        f"  length units: {', '.join(sorted(LENGTH_UNITS))}",
        f"  pressure units: {', '.join(sorted(PRESSURE_UNITS))}",
        f"  density units: {', '.join(sorted(DENSITY_UNITS))}",
        f"  volume units: {', '.join(sorted(VOLUME_UNITS))}",
        f"  temperature units: {', '.join(sorted(TEMPERATURE_UNITS))}",
        f"  time units: {', '.join(sorted(TIME_UNITS))}",
    ]
    return "\n".join(lines)
