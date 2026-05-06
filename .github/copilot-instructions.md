---
applyTo: "**"
---
# Wellsynthai — Project Guardrails

## North Star
A user uploads a PDF → ingestion produces page-level markdown + images + tables + entities → retrieval surfaces the right chunks (text + image refs) → the RAG agent answers from them with citations.

**Anything that does not directly serve this loop is out of scope until further notice.**

## Product Stage (2026-05-06)

We are pivoting from a single-tenant demo to an **internal-clients enterprise product**. This means:

- **Real accounts, real ownership, real persistence.** Per-user document corpora; cross-user isolation enforced at the vector layer too.
- **Supabase is the platform of record.** Postgres (managed) + Auth + Storage buckets + Realtime. We do not roll our own auth, our own bucket service, or our own Postgres.
- **No demo-mode shortcuts.** No SQLite fallback, no "demo seed" routes, no env-fixed sample doc. Every code path assumes a logged-in user with a real `auth.users.id`.
- **Stable document identity.** `documents.id` (uuid) is durable across re-ingestions of the same PDF. `job_id` is per-ingestion-run only; `doc_name` is a display concern.

## Stack Facts (do not re-derive)

- **App layer:** FastAPI service in `app/`, Celery worker in `app/workers/`, Redis as Celery broker + short-lived progress cache.
- **Vectors:** Qdrant — 3 collections (`wellsynthai_chunks`, `wellsynthai_entities`, `wellsynthai_images`). Every point payload carries `owner_id` and `visibility` for tenant isolation.
- **Multimodal LLM:** Gemini (Flash 2.5 + Pro 2.5) for per-page extraction.
- **Persistence (NEW):** Supabase Postgres for `documents`, `jobs`, `briefings`, `lessons`, `pinboards`. Users live in Supabase's managed `auth.users`. Schema migrations via Supabase CLI (`supabase/migrations/`).
- **Storage (NEW):** Supabase Storage buckets replace the `data/output/<doc>/` filesystem. Buckets: `pdfs` (originals), `pages` (rendered PNGs), `artifacts` (markdown / tables / entities JSON). Image serving uses signed URLs.
- **Auth (NEW):** Supabase Auth issues JWTs. Backend verifies via Supabase JWKS; we do **not** implement `/auth/signup` or `/auth/login`. `GET /auth/me` is a thin convenience that returns verified-claims plus our own `users_profile` row (role). Frontend uses `@supabase/supabase-js` directly for sign-in flows.
- **Realtime (NEW):** Supabase Realtime channels on the `jobs` table replace HTTP polling for ingest progress. Frontend subscribes; backend writes status transitions to Postgres and Realtime fan-out is automatic.

## Pipeline Facts (unchanged)

- Ingestion is **per-page streaming** with ~100 concurrent workers (see `PLAN_v3_ingestion.md`). Do not propose batching.
- Page rendering: `pymupdf` `page.get_pixmap(dpi=200).tobytes("png")`, sent inline to Gemini — no Files API, no caching layer.
- Extraction loop per page: `extractor → validator → (loop ≤3) → escalator`. Do not add new stages.

## Hard Rules

- **No new dependencies** beyond what is explicitly approved. Currently approved adds for the Supabase pivot: `supabase`, `asyncpg`, `sqlalchemy>=2.0`, `pyjwt[crypto]`. Anything else needs sign-off.
- **No new endpoints, agents, or config flags** unless the user asks for one by name.
- **No speculative features** — no audio/video, alternate OCR engines, agentic browsing, analytics dashboards beyond what's in the spec.
- **Prefer deletion over addition.** Merge duplicates; do not leave shims or "legacy" wrappers. When migrating to Supabase, delete the filesystem path — do not keep dual-write.
- **Don't beautify code you aren't changing.** No drive-by docstrings, type hints, comments, renames, or reformatting.
- **Don't add error handling for things that can't happen.** Validate at system boundaries (`/ingest`, `/chat`, request middleware) only.
- **Every list endpoint scopes by `owner_id`.** Default `?scope=mine`; `?scope=all` requires `super_admin` role else 403 `requires_super_admin`. No silent fallback.
- **Every Qdrant query carries an `owner_id` / `visibility` filter.** No exceptions. Cross-user leakage is a security bug, not a polish item.

## When in Doubt

1. Trace the live request path before editing:
   `POST /ingest → DB job row insert → Celery dispatch → orchestrator → page_streamer → extractor/validator/escalator → postprocessor → Supabase Storage write + Qdrant index → Realtime status update`
   `POST /chat → rag_agent (with owner_id filter) → rag_tools → reranker → answer`
2. If a function is not on that path and not called by something on that path, it is a deletion candidate — flag it, don't extend it.
3. If a question is "should I add X?", the default answer is **no**.
4. If the change touches identity, ownership, or storage location: it must respect `owner_id` and use Supabase. Filesystem writes under `data/output/` are legacy; new code does not add to that directory.

## Migration Notes (active work)

- Existing `data/output/<doc>/` content is **legacy** until the storage migration runs. New ingestions write to Supabase Storage; old paths remain readable until backfill completes.
- Existing `JobManifest` JSON files are mirrored into the `jobs` Postgres table. The DB row is the source of truth post-migration; Redis stays only as Celery broker + short-lived progress cache.
- `doc_name` (the legacy `f"{safe_stem}_{job_id[:8]}"` slug) is deprecated. New code uses `documents.id` as the stable identifier and `documents.filename` for display.
