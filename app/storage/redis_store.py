"""Redis-backed job manifest store.

Replaces the in-memory ``_jobs`` dict with durable Redis HASHes so that
job state survives app/worker restarts and is visible across containers.

Each job is stored as a Redis HASH at key ``job:{job_id}`` with fields
matching ``JobResult.to_summary()`` (JSON-encoded for nested values).

Redis db2 is used (see docker-compose.yml ``REDIS_URL``).
"""

from __future__ import annotations

import json
import os
from typing import Any

import redis

from app.utils.logging import get_logger

logger = get_logger(__name__)

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/2")
_KEY_PREFIX = "job:"
_INDEX_KEY = "jobs:index"  # sorted set: job_id → created_ts

# Module-level singleton: redis.from_url internally uses a connection pool,
# so a single shared client object reuses sockets across all callers and
# avoids the OSError: Too many open files failure mode under burst load.
_client_singleton: redis.Redis | None = None


def _client() -> redis.Redis:
    """Return the shared Redis client (lazy, connection-pooled)."""
    global _client_singleton  # noqa: PLW0603
    if _client_singleton is None:
        _client_singleton = redis.from_url(_REDIS_URL, decode_responses=True)
    return _client_singleton


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def save_job(job_id: str, data: dict[str, Any]) -> None:
    """Persist a full job summary dict to Redis.

    Flat scalar fields are stored as HASH fields directly.
    Nested dicts/lists are JSON-encoded.
    """
    r = _client()
    flat: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            flat[k] = json.dumps(v, default=str)
        elif v is None:
            flat[k] = ""
        else:
            flat[k] = str(v)

    pipe = r.pipeline()
    pipe.hset(f"{_KEY_PREFIX}{job_id}", mapping=flat)
    # Track in sorted set for list_jobs (score = 0 means insertion order via ZADD NX)
    import time
    pipe.zadd(_INDEX_KEY, {job_id: time.time()}, nx=True)
    pipe.execute()


def update_job_field(job_id: str, field: str, value: Any) -> None:
    """Update a single field on an existing job manifest."""
    r = _client()
    if isinstance(value, (dict, list)):
        r.hset(f"{_KEY_PREFIX}{job_id}", field, json.dumps(value, default=str))
    elif value is None:
        r.hset(f"{_KEY_PREFIX}{job_id}", field, "")
    else:
        r.hset(f"{_KEY_PREFIX}{job_id}", field, str(value))


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def get_job(job_id: str) -> dict[str, Any] | None:
    """Load a job manifest from Redis, or None if not found."""
    r = _client()
    raw = r.hgetall(f"{_KEY_PREFIX}{job_id}")
    if not raw:
        return None
    return _deserialize(raw)


def list_jobs(limit: int = 100) -> list[dict[str, Any]]:
    """Return most recent jobs (newest first)."""
    r = _client()
    # ZREVRANGE returns highest-score (most recent) first
    job_ids = r.zrevrange(_INDEX_KEY, 0, limit - 1)
    if not job_ids:
        return []

    pipe = r.pipeline()
    for jid in job_ids:
        pipe.hgetall(f"{_KEY_PREFIX}{jid}")
    results = pipe.execute()

    jobs = []
    for raw in results:
        if raw:
            jobs.append(_deserialize(raw))
    return jobs


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

_JSON_FIELDS = {
    "extraction", "phase_timings_ms", "stats",
    "errors", "page_results_summary",
}


def _deserialize(raw: dict[str, str]) -> dict[str, Any]:
    """Convert Redis HASH values back to Python types."""
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k in _JSON_FIELDS:
            try:
                out[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                out[k] = v
        elif v == "":
            out[k] = None
        else:
            # Try to restore ints/floats for known numeric fields
            if k in ("total_pages", "pages_processed"):
                try:
                    out[k] = int(v)
                except ValueError:
                    out[k] = v
            else:
                out[k] = v
    return out
