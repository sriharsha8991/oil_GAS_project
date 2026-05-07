# 01 — Architecture

> _Owns: service topology, where every long-lived process lives, request
> lifecycle, links to deeper pages for each subsystem._

## Services (docker-compose)

| Service | Container | Purpose |
|---|---|---|
| API | `wellsynthai-app` | FastAPI on `:8080`. HTTP surface for FE. |
| Worker | `wellsynthai-celery-worker` | Celery prefork pool. Queues: `extract`, `rasterize`, `aggregate`, `default`. |
| Broker | `wellsynthai-redis` | Celery broker + short-lived progress cache. **Not** a system of record. |
| Vectors | `wellsynthai-qdrant` | 3 collections; tenant-scoped via payload filters. See [05-storage-and-vectors.md](05-storage-and-vectors.md). |

Two managed services live outside docker:

- **Supabase Postgres** — system of record for users, projects, documents, jobs, briefings, lessons, pinboards.
- **Supabase Storage** — `pdfs`, `pages`, `artifacts` buckets. Image serving uses signed URLs.
- **Supabase Auth** — issues JWTs. Backend verifies via JWKS.
- **Resend** — transactional email (invite + resend-invite).
- **Gemini** (Flash 2.5 / Pro 2.5) — multimodal extraction.

## Request lifecycle

```
HTTP → FastAPI → CORSMiddleware → SupabaseAuthMiddleware → router → handler
                                                                ↓
                                                  Postgres / Qdrant / Storage / Celery
```

- `CORSMiddleware`: wide-open today (`allow_origin_regex=".*"`).
- `SupabaseAuthMiddleware`: verifies JWT against Supabase JWKS, loads
  `users_profile` row, populates `request.state.user`. Anonymous paths:
  `/`, `/health`, `/openapi.json`, `/docs`, `/redoc`, `/auth/login`,
  `/auth/refresh`. `OPTIONS` preflights are early-returned before auth.

## Routers (mount order matters because of middleware)

Mount order in [app/main.py](../app/main.py) (line ~132):

1. `auth_router` — `/auth/*`
2. `admin_users_router` — `/admin/users/*`
3. `projects_router` — `/projects/*`
4. `documents_router` — `/documents/*`
5. `ingest_router` — `/ingest/*`
6. `search_router` — `/search`
7. `chat_router` — `/chat`

## Background work (Celery)

- `POST /ingest` writes a `jobs` row, then dispatches a Celery task.
- The orchestrator runs page-streamed extraction (~100 concurrent
  workers per doc). See [03-ingestion-pipeline.md](03-ingestion-pipeline.md).
- Status transitions are written to the Postgres `jobs` row. Frontend
  polls via `GET /ingest/{job_id}` or subscribes via Supabase Realtime
  on the `jobs` table.

## Module map (1-line each)

```
app/
  main.py             — FastAPI app, middleware mount, router include
  config.py           — Settings (pydantic-settings) read from env
  auth/               — JWT verify, middleware, /auth routes, deps, email helper
  routes/             — admin_users, projects, documents, ingest, search, chat
  agents/             — extractor, validator, escalator, rag_agent, rag_tools, prompts
  pipeline/           — orchestrator, page_streamer, postprocessor, preprocessing
  retrieval/          — reranker
  vectorstore/        — chunker, embedder, entity_indexer, image_indexer, qdrant_store
  domain/             — entity_resolver, formations, units, well_taxonomy
  storage/            — file_storage (legacy), redis_store
  workers/            — celery_app, tasks/ingest
  db/                 — engine (asyncpg), models (SQLAlchemy 2.0)
  utils/              — gemini_client, logging, email_validate
  tools/              — apply_migrations, bootstrap_super_admin, e2e_*, api_smoke
```

## Where to look first when debugging

- 4xx auth/permission issues → [02-auth-and-rbac.md](02-auth-and-rbac.md)
- Ingest stuck or failed → [03-ingestion-pipeline.md](03-ingestion-pipeline.md) + container logs of `wellsynthai-celery-worker`
- Wrong/empty chat answer → [04-retrieval-and-chat.md](04-retrieval-and-chat.md)
- Missing image / signed URL 403 → [05-storage-and-vectors.md](05-storage-and-vectors.md)
