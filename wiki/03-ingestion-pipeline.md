# 03 — Ingestion Pipeline

> _Owns: PDF → page-level markdown / images / tables / entities, and
> the Celery topology that drives it._

## Stages (live request path)

```
POST /ingest (or /ingest/upload)
  └─ insert jobs row(s) ── status=queued
  └─ dispatch Celery task
        └─ orchestrator.run_job(document_id, project_id, owner_id, file_path)
             ├─ preprocessing      (validate PDF, render pages PNG via pymupdf @ 200 dpi)
             ├─ page_streamer      (~100 concurrent workers, page-level)
             │     └─ extractor → validator → (loop ≤3) → escalator
             ├─ postprocessor      (aggregate full_document.md, tables, entities)
             ├─ Supabase Storage   (upload pages PNG + artifacts)
             └─ vectorstore        (chunker + embedder + qdrant_store + entity_indexer + image_indexer)
  └─ jobs row → status=succeeded | failed
```

Status writes happen at every transition; FE either polls
`GET /ingest/{job_id}` or subscribes to Supabase Realtime on `jobs`.

## Why per-page streaming and not batch

Spec calls for ~100 concurrent workers per document. PDFs are I/O- and
LLM-bound, not CPU-bound. Batching would:

- Multiply Gemini latency by N pages instead of running them concurrently.
- Lose per-page progress UI.
- Increase tail latency dramatically on large reports.

Do **not** batch. See [PLAN_v3_ingestion.md](../PLAN_v3_ingestion.md).

## Stage details

### Preprocessing

[app/pipeline/preprocessing.py](../app/pipeline/preprocessing.py)

- Validates the file is a real PDF, opens via `pymupdf`.
- Renders each page: `page.get_pixmap(dpi=200).tobytes("png")`.
- PNGs are sent **inline** to Gemini — no Files API, no caching layer.
- Pages are uploaded to Supabase Storage (`pages` bucket).

### Page streamer

[app/pipeline/page_streamer.py](../app/pipeline/page_streamer.py)

- An `asyncio.Semaphore(concurrency)` (default 100) bounds inflight Gemini calls.
- Each page goes through:

```
extractor(page_png, prompt)
  → returns ExtractionResult (markdown + tables + entities)

validator(extraction_result)
  → returns ValidationVerdict {ok|retry|escalate, reason}

if retry and attempts < 3:
  loop with retry_prompt
elif escalate:
  escalator(...)  → uses Pro model with broader prompt
```

Outputs land per-page as `page_NNN.md` + tables JSON/MD + structured entities.

### Extractor / validator / escalator

[app/agents/extractor.py](../app/agents/extractor.py),
[app/agents/validator.py](../app/agents/validator.py),
[app/agents/escalation.py](../app/agents/escalation.py)

- Extractor uses Gemini Flash 2.5 with the page PNG and the system
  prompt in [app/agents/prompts.py](../app/agents/prompts.py).
- Validator runs lightweight checks (was anything extracted? do tables
  parse? are entities sane?) and decides retry/escalate.
- Escalator switches to Pro 2.5 for the same page.
- **Three stages and only three.** Don't add a fourth without removing one.

### Postprocessor

[app/pipeline/postprocessor.py](../app/pipeline/postprocessor.py)

- Concatenates page markdown into `full_document.md`.
- Deduplicates entities via `app/domain/entity_resolver.py`.
- Writes JSON manifests for tables and entities.

### Vectorstore

[app/vectorstore/](../app/vectorstore/)

- `chunker` — splits markdown into ~512-token chunks with overlap.
- `embedder` — uses Gemini text embedding model.
- `qdrant_store` — writes to `wellsynthai_chunks` collection with payload
  `{user_id, role, organization, project_id, document_id, page_num, ...}`.
- `entity_indexer` → `wellsynthai_entities` collection.
- `image_indexer` → `wellsynthai_images` collection (one point per
  rendered page).

See [05-storage-and-vectors.md](05-storage-and-vectors.md) for
collection-by-collection payload contracts.

## Celery topology

[app/workers/celery_app.py](../app/workers/celery_app.py)

- Single broker = Redis. No result backend (we read status from
  Postgres, not Celery).
- Queues: `extract`, `rasterize`, `aggregate`, `default`. The pool
  type is `prefork`.
- Each task disposes its SQLAlchemy async engine in `finally:` to
  prevent the cross-event-loop binding bug we hit earlier.

## What happens on failure

- Any handler exception in the orchestrator → `jobs.status = failed`,
  `jobs.error = <truncated traceback>`. Storage + Qdrant artifacts
  written so far stay (idempotency on retry uses `document_id`).
- Re-running ingest on the same `(project_id, filename)` reuses the
  existing `documents.id` and overwrites artifacts. `job_id` is
  per-run only.

## Observability

Structured logs (structlog) with these key event names worth grepping:

```
ingest_started, page_extracted, page_validation_retry,
page_escalated, postprocess_done, vectorstore_indexed,
ingest_succeeded, ingest_failed
```

`format_exc_info` is enabled — tracebacks are in the log line, not
swallowed.

## Things this page does not own

- Where the bytes physically go → [05-storage-and-vectors.md](05-storage-and-vectors.md).
- The `jobs` table column meaning → [06-database-schema.md](06-database-schema.md).
- How chat consumes these artifacts → [04-retrieval-and-chat.md](04-retrieval-and-chat.md).
