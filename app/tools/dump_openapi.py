"""Dump the FastAPI app's OpenAPI schema to ``frontend_docs/openapi.json``.

C-01: frontend codegen anchors on this file. Run after any route change:

    python -m app.tools.dump_openapi

Writes a stable JSON snapshot (sorted keys, 2-space indent) so diffs in PRs
are reviewable.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.main import app


def main() -> None:
    out = Path(__file__).resolve().parents[2] / "frontend_docs" / "openapi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    schema = app.openapi()
    out.write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out} ({out.stat().st_size:,} bytes, {len(schema.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
