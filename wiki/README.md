# Wellsynthai Backend Wiki

This wiki is the engineering source-of-truth for the Wellsynthai backend.
Each page owns one slice of the system. If you update behaviour, update
the page that owns it — do not duplicate facts across pages.

## Pages

| # | Page | Owns |
|---|---|---|
| 01 | [Architecture](01-architecture.md) | Service topology, request lifecycle, where things live |
| 02 | [Auth & RBAC](02-auth-and-rbac.md) | Tiers, JWT verification, invite flow, quotas, error codes |
| 03 | [Ingestion Pipeline](03-ingestion-pipeline.md) | PDF → markdown/images/tables/entities; Celery topology |
| 04 | [Retrieval & Chat](04-retrieval-and-chat.md) | RAG agent, tools, reranker, image grounding, soft-land |
| 05 | [Storage & Vectors](05-storage-and-vectors.md) | Supabase Storage buckets + Qdrant collections + tenant payload |
| 06 | [Database Schema](06-database-schema.md) | Postgres tables, enums, triggers, RLS |
| 07 | [API Reference](07-api-reference.md) | Endpoint-by-endpoint contract; canonical request/response |
| 08 | [Deployment & Ops](08-deployment-and-ops.md) | Docker stack, env, scaling knobs, log channels |
| 09 | [Testing](09-testing.md) | Unit, e2e, smoke; how to add a new test |
| 10 | [Runbooks](10-runbooks.md) | Bootstrap super_admin, recover lost password, reset Qdrant, replay an ingest |

## How this wiki is maintained

- **One page = one responsibility.** If a page is tempted to explain
  something owned by another page, link to that page instead of
  copy-pasting.
- **Code is the source of truth, not the wiki.** The wiki explains
  *why* and *how to operate*. For *what exactly the code does*, link to
  the file/line and let the reader read it.
- **Date every behaviour change.** When you change a contract, prepend
  the section's first paragraph with `> _Changed YYYY-MM-DD: ..._`.
- **Wiki edits ship in the same PR as the code change.** A code change
  that contradicts the wiki is a regression.

## Where this wiki ends and other docs begin

- `README.md` (repo root) — quickstart for new contributors. Stays short.
- `frontend_docs/communication_from_backend.md` — backend → frontend
  contract. **Authoritative for the FE.** This wiki must not contradict
  it; if something here disagrees, the FE doc wins and we open a wiki
  issue to reconcile.
- `BACKEND_SPEC.md`, `PLAN*.md`, `DEV_LOG.md` — historical / planning.
  Do not treat as live.
- `.github/copilot-instructions.md` — project guardrails for AI agents
  and humans alike. Read once on day one.
