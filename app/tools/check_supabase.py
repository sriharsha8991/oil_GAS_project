"""Sanity-check the Supabase environment before opening PR-5.

Run with:  python -m app.tools.check_supabase

Verifies, in order:
1. Required env vars are present.
2. JWKS endpoint returns at least one signing key.
3. Postgres connection (FastAPI app pool) succeeds and returns server version.
4. Postgres connection (Celery worker pool) succeeds.
5. Service-role key authenticates against the Supabase REST root.

No code in `app/` is touched. This script exists only to fail fast on
misconfiguration so we don't waste a PR-5 cycle on env drift.
"""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg
import httpx
from dotenv import load_dotenv

REQUIRED_VARS = [
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_JWKS_URL",
    "DATABASE_URL",
    "WORKER_DATABASE_URL",
]


def _to_asyncpg_dsn(url: str) -> str:
    """SQLAlchemy uses postgresql+asyncpg://; asyncpg.connect wants postgresql://."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _check_env() -> dict[str, str]:
    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        print(f"FAIL  env: missing {missing}")
        sys.exit(1)
    print("PASS  env: all required vars present")
    return {v: os.environ[v] for v in REQUIRED_VARS}


async def _check_jwks(url: str) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
    if r.status_code != 200:
        print(f"FAIL  jwks: {url} -> HTTP {r.status_code}")
        sys.exit(1)
    keys = r.json().get("keys", [])
    if not keys:
        print("FAIL  jwks: response has no keys")
        sys.exit(1)
    algs = {k.get("alg") for k in keys}
    print(f"PASS  jwks: {len(keys)} key(s), algs={algs}")


async def _check_pg(label: str, dsn: str) -> None:
    try:
        conn = await asyncpg.connect(_to_asyncpg_dsn(dsn), timeout=10)
    except Exception as exc:
        print(f"FAIL  pg ({label}): {type(exc).__name__}: {exc}")
        sys.exit(1)
    try:
        version = await conn.fetchval("SELECT version()")
        who = await conn.fetchval("SELECT current_user")
        db = await conn.fetchval("SELECT current_database()")
    finally:
        await conn.close()
    short = version.split(",")[0] if version else "?"
    print(f"PASS  pg ({label}): {short} as {who}@{db}")


async def _check_service_role(supabase_url: str, key: str) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{supabase_url}/rest/v1/",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
        )
    if r.status_code not in (200, 404):
        print(f"FAIL  service_role: HTTP {r.status_code} {r.text[:120]}")
        sys.exit(1)
    print(f"PASS  service_role: REST root reachable (HTTP {r.status_code})")


async def main() -> None:
    load_dotenv()
    env = _check_env()
    await _check_jwks(env["SUPABASE_JWKS_URL"])
    await _check_pg("app", env["DATABASE_URL"])
    await _check_pg("worker", env["WORKER_DATABASE_URL"])
    await _check_service_role(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])
    print("\nAll checks passed. Safe to open PR-5.")


if __name__ == "__main__":
    asyncio.run(main())
