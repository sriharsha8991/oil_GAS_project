"""Celery application — Tier 3 task broker (§4.L1.1.d).

This is the entrypoint referenced by the celery-worker service in
docker-compose.yml:  ``celery -A app.workers.celery_app worker ...``

Queues
------
- extract     — per-page Gemini extraction tasks (future)
- rasterize   — PDF-to-PNG rendering tasks (future)
- aggregate   — document-level aggregation tasks (future)
- default     — catch-all

Task modules will be registered here as they are implemented.
"""

import os

from celery import Celery

broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

celery_app = Celery(
    "wellsynthai",
    broker=broker_url,
    backend=result_backend,
    include=["app.workers.tasks.ingest", "app.workers.tasks.memory"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,          # §4.L1.1.e — ack only after task completes
    worker_prefetch_multiplier=1, # §4.L1.1.n pitfall #7 — one task at a time
    task_reject_on_worker_lost=True,
    task_default_queue="default",
    task_queues={
        "extract": {},
        "rasterize": {},
        "aggregate": {},
        "default": {},
    },
)

# Task modules are registered via the `include` parameter above.
