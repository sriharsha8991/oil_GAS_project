"""SSE wire-format helper.

The chat streaming endpoint sends one JSON object per SSE message using the
plain ``data:``-only shape. EventSource clients can parse this without
needing custom ``event:`` listeners; ``fetch``-based clients on the FE do
the same. See wiki/11 for the contract.
"""

from __future__ import annotations

import json
from typing import Any


def sse_pack(payload: dict[str, Any]) -> bytes:
    """Encode a JSON payload as a single SSE ``data:`` frame."""
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode("utf-8")
