"""Bidirectional unit conversion for oilfield quantities.

All conversions are exact (no floating-point drift beyond IEEE 754
double precision).  Every converter is a pure function: input value in,
output value out — no side effects.

Usage:
    from app.domain.units import convert
    meters = convert(1500, "ft", "m")          # → 457.2
    ppg    = convert(1200, "kg/m3", "ppg")     # → 10.01
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Conversion factors: target_unit = source_unit * factor
# Stored as (from, to) → multiplicative factor.
# ---------------------------------------------------------------------------

_CONVERSIONS: dict[tuple[str, str], float] = {
    # Length
    ("ft", "m"): 0.3048,
    ("m", "ft"): 1 / 0.3048,
    ("in", "mm"): 25.4,
    ("mm", "in"): 1 / 25.4,
    ("in", "cm"): 2.54,
    ("cm", "in"): 1 / 2.54,
    ("ft", "in"): 12.0,
    ("in", "ft"): 1 / 12.0,
    ("m", "cm"): 100.0,
    ("cm", "m"): 0.01,
    ("m", "mm"): 1000.0,
    ("mm", "m"): 0.001,

    # Pressure
    ("psi", "kPa"): 6.894757,
    ("kPa", "psi"): 1 / 6.894757,
    ("psi", "MPa"): 0.006894757,
    ("MPa", "psi"): 1 / 0.006894757,
    ("psi", "bar"): 0.0689476,
    ("bar", "psi"): 1 / 0.0689476,
    ("bar", "kPa"): 100.0,
    ("kPa", "bar"): 0.01,
    ("atm", "psi"): 14.696,
    ("psi", "atm"): 1 / 14.696,
    ("atm", "kPa"): 101.325,
    ("kPa", "atm"): 1 / 101.325,

    # Density (mud weight)
    ("ppg", "kg/m3"): 119.826427,
    ("kg/m3", "ppg"): 1 / 119.826427,
    ("ppg", "sg"): 0.119826427,
    ("sg", "ppg"): 1 / 0.119826427,
    ("sg", "kg/m3"): 1000.0,
    ("kg/m3", "sg"): 0.001,
    ("ppg", "lb/ft3"): 7.48052,
    ("lb/ft3", "ppg"): 1 / 7.48052,

    # Volume
    ("bbl", "m3"): 0.158987,
    ("m3", "bbl"): 1 / 0.158987,
    ("bbl", "gal"): 42.0,
    ("gal", "bbl"): 1 / 42.0,
    ("bbl", "L"): 158.987,
    ("L", "bbl"): 1 / 158.987,
    ("gal", "L"): 3.78541,
    ("L", "gal"): 1 / 3.78541,
    ("m3", "L"): 1000.0,
    ("L", "m3"): 0.001,

    # Temperature is handled separately (not multiplicative)
}


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert a numeric value between two compatible units.

    Args:
        value: The numeric value to convert.
        from_unit: Source unit string (must match enum values in well_taxonomy).
        to_unit: Target unit string.

    Returns:
        Converted value.

    Raises:
        ValueError: If the unit pair is not supported.
    """
    if from_unit == to_unit:
        return value

    # Temperature — offset + scale conversions
    pair = (from_unit.upper(), to_unit.upper())
    if pair == ("F", "C"):
        return (value - 32) * 5 / 9
    if pair == ("C", "F"):
        return value * 9 / 5 + 32

    factor = _CONVERSIONS.get((from_unit, to_unit))
    if factor is not None:
        return value * factor

    raise ValueError(
        f"No conversion defined for ({from_unit!r} → {to_unit!r}). "
        f"Supported pairs: {sorted(_CONVERSIONS.keys())}"
    )


def can_convert(from_unit: str, to_unit: str) -> bool:
    """Check whether a conversion path exists between two units."""
    if from_unit == to_unit:
        return True
    if (from_unit.upper(), to_unit.upper()) in (("F", "C"), ("C", "F")):
        return True
    return (from_unit, to_unit) in _CONVERSIONS


# ---------------------------------------------------------------------------
# Canonical unit per quantity type — used for normalization
# ---------------------------------------------------------------------------

CANONICAL_UNITS: dict[str, str] = {
    "length": "ft",
    "depth": "ft",
    "pressure": "psi",
    "density": "ppg",
    "mud_weight": "ppg",
    "volume": "bbl",
    "temperature": "F",
    "diameter": "in",
}


def to_canonical(value: float, from_unit: str, quantity_type: str) -> tuple[float, str]:
    """Convert a value to the canonical unit for its quantity type.

    Args:
        value: Numeric value.
        from_unit: Current unit.
        quantity_type: One of the keys in CANONICAL_UNITS.

    Returns:
        (converted_value, canonical_unit) tuple.

    Raises:
        ValueError: If quantity_type is unknown or conversion is impossible.
    """
    canonical = CANONICAL_UNITS.get(quantity_type)
    if canonical is None:
        raise ValueError(f"Unknown quantity type: {quantity_type!r}")
    return convert(value, from_unit, canonical), canonical


# ---------------------------------------------------------------------------
# Physical range sanity checks (for numeric guardrail A5)
# ---------------------------------------------------------------------------

# Reasonable ranges per quantity + unit. Values outside these are flagged
# as potential hallucinations. Ranges are intentionally wide to avoid
# false positives — they catch only physically impossible values.
_SANE_RANGES: dict[str, tuple[float, float]] = {
    # depths
    "ft": (0, 50_000),       # deepest wells ~40k ft
    "m": (0, 15_000),        # ~15 km
    # pressures
    "psi": (0, 30_000),
    "kPa": (0, 210_000),
    "MPa": (0, 210),
    "bar": (0, 2_100),
    # densities (mud weight)
    "ppg": (6.0, 22.0),      # water ≈ 8.33 ppg; heaviest mud ~20 ppg
    "kg/m3": (700, 2_700),
    "sg": (0.7, 2.7),
    # volumes
    "bbl": (0, 100_000),
    "m3": (0, 16_000),
    # temperature
    "F": (-60, 600),
    "C": (-50, 315),
    # diameters (casing / bit)
    "in": (0, 48),
    "mm": (0, 1_220),
}


def is_sane(value: float, unit: str) -> bool:
    """Check whether a value is within physically plausible range for its unit."""
    bounds = _SANE_RANGES.get(unit)
    if bounds is None:
        return True  # unknown unit → don't flag
    lo, hi = bounds
    return lo <= value <= hi
