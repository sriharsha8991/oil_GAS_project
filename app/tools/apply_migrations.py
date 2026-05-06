"""Apply pending SQL migrations to Supabase Postgres.

Reads .sql files in ``supabase/migrations/`` (sorted by filename) and
executes each via asyncpg. Tracks applied migrations in a small
``_app_migrations`` table so re-runs are idempotent.

Usage:  python -m app.tools.apply_migrations
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


def _to_asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def main() -> None:
    load_dotenv()
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set")

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print("no migrations found")
        return

    conn = await asyncpg.connect(_to_asyncpg_dsn(dsn))
    try:
        await conn.execute(
            """
            create table if not exists public._app_migrations (
                name text primary key,
                applied_at timestamptz not null default now()
            )
            """
        )
        applied = {
            r["name"]
            for r in await conn.fetch("select name from public._app_migrations")
        }

        for path in files:
            if path.name in applied:
                print(f"skip  {path.name}")
                continue
            print(f"apply {path.name}")
            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "insert into public._app_migrations (name) values ($1)",
                    path.name,
                )
        print("migrations done")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
