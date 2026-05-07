# 08 — Deployment & Ops

> _Owns: how to run the stack, what env vars are required, scaling
> knobs, log channels._

## Local stack (docker-compose)

```powershell
docker compose up -d
docker compose ps
docker logs -f wellsynthai-app
```

Services come up in order: `wellsynthai-redis`, `wellsynthai-qdrant`,
`wellsynthai-app`, `wellsynthai-celery-worker`. Health check on the API is `GET /health`.

Source code is **live-mounted** into the API and worker containers
(`./app:/app/app`). Code changes pick up on container restart:

```powershell
docker restart wellsynthai-app
docker restart wellsynthai-celery-worker
```

## Required env (.env)

Copy [.env.example](../.env.example) to `.env` and fill in:

| Var | Purpose |
|---|---|
| `SUPABASE_URL` | `https://<ref>.supabase.co` |
| `SUPABASE_ANON_KEY` | Public anon key — used for `/auth/login`, `/auth/refresh` |
| `SUPABASE_SERVICE_ROLE_KEY` | Service-role key — used for admin ops; NEVER ship to FE |
| `SUPABASE_JWKS_URL` | `<project>/auth/v1/jwks` |
| `DATABASE_URL` | `postgresql+asyncpg://postgres:<pw>@<host>:6543/postgres` (pooled) |
| `WORKER_DATABASE_URL` | Same as `DATABASE_URL` (workers create/dispose engine per task) |
| `REDIS_URL` | `redis://wellsynthai-redis:6379/0` inside docker |
| `QDRANT_URL` | `http://wellsynthai-qdrant:6333` inside docker |
| `GEMINI_API_KEY` | Multimodal extraction + chat |
| `RESEND_API_KEY` | Outbound email for invites |
| `RESEND_FROM_EMAIL` | Default `no-reply@rax-resume.xyz` (verified domain) |
| `RESEND_FROM_NAME` | Default `Wellsynth` |

Optional: `LOG_LEVEL` (default `INFO`), `CORS_ALLOW_ORIGINS` (currently
ignored — middleware uses `allow_origin_regex=".*"`).

## First-time bootstrap on a new environment

1. Apply migrations:
   ```powershell
   .\.venv\Scripts\python.exe -m app.tools.apply_migrations
   ```
2. Bootstrap a super_admin (CLI shown in [10-runbooks.md](10-runbooks.md)).
3. Verify Resend domain is verified in the dashboard.
4. Run smoke: `python -m app.tools.api_smoke` → expect 23/23 PASS.

## Scaling knobs

| Knob | Default | Where |
|---|---|---|
| Page-stream concurrency | 100 | [app/pipeline/page_streamer.py](../app/pipeline/page_streamer.py) (`PAGE_STREAM_CONCURRENCY`) |
| Celery worker count | 1 (dev) | `docker-compose.yml` services replicas; or `--concurrency=N` in CMD |
| Gunicorn/Uvicorn workers | 2 (dev) | `docker-compose.yml` API CMD |
| Qdrant HNSW M / ef_construct | defaults | [app/vectorstore/qdrant_store.py](../app/vectorstore/qdrant_store.py) |
| Reranker top-K | 6 | [app/retrieval/reranker.py](../app/retrieval/reranker.py) |

## Logs & observability

- App logs: `docker logs wellsynthai-app` (structlog JSON-ish key=value).
- Worker logs: `docker logs wellsynthai-celery-worker`.
- Redis: `docker logs wellsynthai-redis` — only error if the broker dies.
- Qdrant: `docker logs wellsynthai-qdrant` — useful when payload index errors.

Search-friendly log keys to grep:

```
auth_login_success, auth_login_failed, auth_refresh_failed,
ingest_started, ingest_succeeded, ingest_failed,
page_extracted, page_validation_retry, page_escalated,
chat_agent_error, chat_soft_landed,
resend_send_failed, invite_email_sent
```

## Hot paths to monitor

- `chat_agent_error` rate > 0 → planner or tool failure; correlate with
  Gemini quota errors.
- `resend_send_failed` rate > 0 → from-domain unverified or quota
  exhausted.
- Long-tail page extraction (>30s/page) → escalator overuse; check
  validator thresholds.

## Things this page does not own

- Database migration mechanics → [10-runbooks.md](10-runbooks.md).
- Test commands → [09-testing.md](09-testing.md).
