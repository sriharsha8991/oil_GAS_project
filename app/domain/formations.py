"""Formation alias registry — maps regional naming variants to canonical names.

Loaded from a config dict (or future JSON/YAML file per asset). Matching
is case-insensitive with whitespace normalization.

Usage:
    from app.domain.formations import FormationRegistry
    reg = FormationRegistry()
    canonical = reg.resolve("EagleFord Shale")  # → "Eagle Ford"
"""

from __future__ import annotations

import re
from typing import Any


# Default alias table — extend per asset config or load from JSON.
# Key = canonical name, value = list of known aliases (case-insensitive).
_DEFAULT_ALIASES: dict[str, list[str]] = {
    "Eagle Ford": [
        "eagle ford",
        "eagleford",
        "eagle ford shale",
        "eagleford shale",
        "ef shale",
    ],
    "Permian Basin": [
        "permian",
        "permian basin",
    ],
    "Wolfcamp": [
        "wolfcamp",
        "wolfcamp shale",
        "wolfcamp a",
        "wolfcamp b",
        "wolfcamp c",
        "wolfcamp d",
    ],
    "Bone Spring": [
        "bone spring",
        "bone springs",
        "bonespring",
        "bone spring formation",
    ],
    "Spraberry": [
        "spraberry",
        "spraberry trend",
        "spraberry formation",
    ],
    "Austin Chalk": [
        "austin chalk",
        "austinchalk",
        "austin chalk formation",
    ],
    "Bakken": [
        "bakken",
        "bakken shale",
        "bakken formation",
    ],
    "Three Forks": [
        "three forks",
        "threeforks",
        "three forks formation",
    ],
    "Marcellus": [
        "marcellus",
        "marcellus shale",
    ],
    "Haynesville": [
        "haynesville",
        "haynesville shale",
    ],
    "Niobrara": [
        "niobrara",
        "niobrara chalk",
        "niobrara formation",
    ],
    "Woodford": [
        "woodford",
        "woodford shale",
    ],
    "Barnett": [
        "barnett",
        "barnett shale",
    ],
    "Utica": [
        "utica",
        "utica shale",
        "utica/point pleasant",
    ],
    "SCOOP": [
        "scoop",
        "south central oklahoma oil province",
    ],
    "STACK": [
        "stack",
        "sooner trend anadarko basin canadian and kingfisher",
    ],
    "Midland Basin": [
        "midland basin",
        "midland",
    ],
    "Delaware Basin": [
        "delaware basin",
        "delaware",
    ],
}


def _normalize(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for comparison."""
    return re.sub(r"\s+", " ", text.lower().strip())


class FormationRegistry:
    """Resolve formation name variants to canonical names.

    The registry is initialized with a default alias table and can be
    extended with asset-specific aliases.
    """

    def __init__(self, extra_aliases: dict[str, list[str]] | None = None) -> None:
        # Build lookup: normalized alias → canonical name
        self._lookup: dict[str, str] = {}
        self._load_aliases(_DEFAULT_ALIASES)
        if extra_aliases:
            self._load_aliases(extra_aliases)

    def _load_aliases(self, aliases: dict[str, list[str]]) -> None:
        for canonical, variants in aliases.items():
            norm_canonical = _normalize(canonical)
            self._lookup[norm_canonical] = canonical
            for variant in variants:
                self._lookup[_normalize(variant)] = canonical

    def resolve(self, name: str) -> str:
        """Resolve a formation name to its canonical form.

        Returns the input unchanged if no alias match is found.
        """
        return self._lookup.get(_normalize(name), name)

    def is_known(self, name: str) -> bool:
        """Check whether a formation name (or alias) is in the registry."""
        return _normalize(name) in self._lookup

    @property
    def canonical_names(self) -> list[str]:
        """Return sorted list of all canonical formation names."""
        return sorted(set(self._lookup.values()))

    def add_alias(self, canonical: str, alias: str) -> None:
        """Register a new alias for an existing or new canonical name."""
        norm_canonical = _normalize(canonical)
        if norm_canonical not in self._lookup:
            self._lookup[norm_canonical] = canonical
        self._lookup[_normalize(alias)] = canonical
