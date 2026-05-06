"""One-shot Qdrant wipe + re-create — destructive.

Usage:
    python -m app.tools.qdrant_reset --yes

Deletes all 3 Wellsynthai collections and recreates them with the
current tenant payload indices. Use after a tenant-scope schema change
(PR-7) when the safest path is wipe-and-reingest.
"""

from __future__ import annotations

import argparse
import asyncio

from app.config import get_settings
from app.vectorstore.qdrant_store import QdrantVectorStore


async def _run() -> None:
    settings = get_settings()
    store = QdrantVectorStore(settings)
    await store.reset_all()
    print("Qdrant collections wiped and recreated.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes", action="store_true", required=True,
        help="Confirm destructive action.",
    )
    parser.parse_args()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
