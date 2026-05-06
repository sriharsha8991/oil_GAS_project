# WellSynth AI — Backend → Frontend Communication

> **Purpose:** Living document the **backend team** uses to respond to the frontend team. Mirrors [BACKEND_REPORT.md](BACKEND_REPORT.md) (which is frontend → backend). Forward-replace this doc whenever a backend commitment changes status.
>
> **How to read:** §1 is the executive summary. §2 is the canonical commitment list (versioned action items). §3 answers every open question raised in `BACKEND_REPORT.md`. §4 is per-phase commentary. §5 covers the role-aware dashboard endorsement. §6 lists what we want from frontend in return.
>
> **Last updated:** 2026-05-06 — **v2.4**. **PR-7 (Projects + Documents + tenant-scoped vectors) shipped.** `owner_id`/`doc_id`/`job_id` are no longer part of any frontend-visible contract; identity is now `(user_id, project_id, document_id)` end-to-end. Qdrant was wiped and re-created with tenant payload indices. Storage paths are now `<bucket>/<user_id>/<project_id>/<document_id>/<file>`. Realtime channel + Postgres `jobs` row replace Redis polling. **Frontend must update: chat scope params, ingest contract, document/project endpoints. See §0 PR-7 contract block below.**
> **Owner:** Backend team.
> **Status:** §2.7 PR-5 ✅ | §2.7.1 PR-5.1 ✅ | §2.8 PR-6 ✅ | **§2.10 PR-7 ✅** (new). §2.9 still Open.

---

## 0. PR-7 Contract Changes (frontend MUST read first)

Effective 2026-05-06. The previous demo-shaped contract (`owner_id`, `doc_id`, `doc_name`, job-keyed image URLs) is gone.

### 0.1 Identity model

- **`user_id`** — `auth.users.id` (uuid). Always derived from the JWT, never sent in request bodies.
- **`project_id`** — uuid. A user owns N projects. Documents belong to exactly one project.
- **`document_id`** — uuid. Stable across re-ingestions of the same PDF (resolves the old §3.4 `doc_name` instability problem at the data-model level).
- **`job_id`** — uuid. Per-ingestion-run only. Used to drive progress UI; not for citations or links.

`owner_id` and `doc_id` no longer exist anywhere in the API. Remove all references.

### 0.2 New endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST`   | `/projects`                                       | Create project `{ name, description? }` → `{ id, ... }` |
| `GET`    | `/projects`                                       | List the calling user's projects |
| `GET`    | `/projects/{id}`                                  | Project detail |
| `PATCH`  | `/projects/{id}`                                  | Update `{ name?, description? }` |
| `DELETE` | `/projects/{id}`                                  | Delete + cascade documents/jobs/Storage/Qdrant |
| `GET`    | `/documents?project_id={id}`                      | List user's documents (optional project filter) |
| `GET`    | `/documents/{id}`                                 | Document metadata + `latest_job` |
| `DELETE` | `/documents/{id}`                                 | Delete + cascade Storage/Qdrant |
| `GET`    | `/documents/{id}/full`                            | Full markdown |
| `GET`    | `/documents/{id}/pages/{n}/image`                 | 302 → signed PNG URL |
| `GET`    | `/documents/{id}/pages/{n}/image/crop?ymin&xmin&ymax&xmax` | Bbox crop PNG |

### 0.3 Renamed / removed

- `GET /ingest/{job_id}/full` — **REMOVED.** Use `GET /documents/{id}/full`.
- `GET /ingest/{job_id}/pages/{n}/image` — **REMOVED.** Use `GET /documents/{id}/pages/{n}/image`.
- `GET /ingest/{job_id}/pages/{n}/image/crop` — **REMOVED.** Use `GET /documents/{id}/pages/{n}/image/crop`.
- `POST /chat` body — `job_id` and `doc_name` **REMOVED**. New optional fields: `project_id`, `document_id` (both uuid). `/chat` now requires authentication.
- `GET /search` — `job_id` and `doc_name` query params **REMOVED**. New optional filters: `project_id`, `document_id`. Auth required.

### 0.4 Changed contracts

- **`POST /ingest`** body: now requires `project_id: uuid`. Shape: `{ project_id, file_paths: string[] }`. Each item in the response includes `{ job_id, document_id, filename, status }`.
- **`POST /ingest/upload`** multipart: now requires a `project_id` form field alongside `files`. Response same as `/ingest`.
- **`GET /ingest`** — now scoped to the calling user; sources from Postgres `jobs` table (not Redis). Each row: `{ job_id, document_id, filename, status, current_phase, pages_total, pages_done, error, created_at, started_at, ended_at }`.
- **`GET /ingest/{job_id}`** — sources from Postgres; same row shape as above. 404 if not yours (super_admin sees all).
- **Chat sources / evidence** — every `source` and `evidence` item now carries `document_id`. Image-evidence URLs are now `/documents/{id}/pages/{n}/image[?crop...]`. The `job_id` field on sources is still echoed but **must not be used for links**; treat it as opaque debug info.
- **Attached pages** in chat response: `attached_pages[].url` is now `/documents/{id}/pages/{n}/image`. The legacy `job_id` key on each item is replaced by `document_id`.
- **`GET /auth/me`** — now includes `organization: string | null`.
- **`PATCH /auth/me/profile`** — body accepts `{ display_name?, organization? }`; only fields supplied are updated.
- **`GET /admin/users`** — each row now includes `organization: string | null`.

### 0.5 Realtime + polling

- **Use Supabase Realtime** on the `jobs` table to drive ingest progress. Filter by `user_id = auth.uid()` (RLS already enforces this). The `jobs` row is the source of truth — `status ∈ {queued, running, succeeded, failed}`, plus `current_phase`, `pages_total`, `pages_done`, `error`.
- The Redis-backed polling endpoint (`GET /ingest/{job_id}` returning a manifest) is **still available** as a fallback, but it now reads from Postgres and is sufficient if you don't want to subscribe.

### 0.6 Tenant payload (informational)

Every Qdrant point now carries `{ user_id, role, organization, project_id, document_id }` plus the existing artifact metadata. This is enforced server-side; frontend never sees raw Qdrant payloads, but knowing it exists explains why cross-user leakage is impossible — every search goes through a mandatory `user_id` filter.

### 0.7 Migration checklist for the FE

1. Replace every `job_id` used in citation links / image URLs with `document_id` from the new `sources[i].document_id` field.
2. Update any "ingest a file" form to require/select a `project_id` first; show an empty-state CTA "Create your first project" when the user has none.
3. Add a Projects page (list + create + delete) and a Documents page (list filtered by project + delete).
4. Replace the `refetchInterval` polling on ingest progress with a Supabase Realtime subscription on `jobs` (still polling-acceptable as a fallback).
5. Surface `organization` on the profile screen + admin user-management screen.
6. Drop any code that ever sent `owner_id`, `doc_id`, `doc_name`, or `job_id` in a request body.

---

---

## 1. Executive Summary

The frontend team's docs (BACKEND_REPORT, IMPLEMENTATION_PLAN, ROLES_AND_DASHBOARDS) are **accepted in full**. No objections to MOD-1 through MOD-12. The role-aware dashboard direction and §5 visual refinement are endorsed.

**TL;DR for the frontend team:**

- **All 11 original backend asks (B-001 → B-011) accepted.** 5 land this week, 3 land alongside specific PRs, 2 land before demo, 1 deferred post-demo.
- **One critical heads-up:** `doc_name` is **not stable** across re-ingestion of the same PDF (intentional). Group by `filename` for any cross-ingest aggregation. Details in [§3.4](#34-doc_name-is-not-deterministic).
- **One existing bug we'll fix:** current backend CORS is `allow_origins=["*"]` with `allow_credentials=True`, which browsers reject per spec. Tightening to env-driven allow-list this week.
- **One spec extension we volunteer:** `/chat` will gain `job_ids: string[] | null` in PR-3 (removes the §28 v1 single-doc limitation for "Chat with this board").
- **One persona suggestion:** keep **Wellsite Supervisor** on the roadmap as the operational counterpart to Drilling Engineer (planning vs. live ops). Defer for v1; revisit post-demo.

Build order remains **PR-1 → PR-2 → PR-3 → PR-4** aligned to **P1b → P2 → P3 → P3**, exactly as IMPLEMENTATION_PLAN §7 sequences it.

### 1.1 Architectural pivot — Supabase-native, no demo posture

Product decision (2026-05-06): we are no longer building toward a public demo. We are building an **enterprise product for internal clients first**. This collapses several earlier debates:

- **Supabase replaces** the SQLite/Postgres-direct + passlib + itsdangerous + custom-`/auth/*` stack. We get Postgres + Auth + Storage buckets + Realtime in one platform with one vendor relationship.
- **No demo seeds, no demo-mode shortcuts.** Every code path assumes a real authenticated user. The `admin@demo` / `engineer@demo` plan in v1.1 is dropped — internal client onboarding is via Supabase Auth invitations.
- **Filesystem `data/output/` is legacy.** New ingestions write to Supabase Storage buckets (`pdfs`, `pages`, `artifacts`). Existing outputs stay readable until backfill (C-46).
- **Document identity becomes durable.** A new `documents` table holds a stable `documents.id` (uuid) keyed by content hash. Re-ingesting the same PDF reuses the document id; only `job_id` changes per run. This **resolves the `doc_name` instability problem** raised in [§3.4](#34-doc_name-is-not-deterministic) at the data-model level rather than as a frontend workaround.
- **Polling dies.** Frontend subscribes to Supabase Realtime channels on the `jobs` table; backend writes status transitions to Postgres and Realtime fans them out. The `refetchInterval: 2000` polling pattern in IMPLEMENTATION_PLAN P1a is replaced.

The new sequence is **PR-5 (Auth + DB) → PR-6 (Storage migration) → PR-7 (Document persistence + Realtime) → PR-1…PR-4** (the original feature PRs, now landing already-Supabase-native).

---

## 2. Backend Commitment List (canonical)

This is the versioned to-do for the backend team. Each item has an ID, a target window, and a one-line acceptance criterion. Status flips as PRs merge.

### 2.1 This week (no PR dependency)

| ID | Item | Acceptance | Status |
|---|---|---|---|
| **C-01** | Generate + commit `openapi.json` to repo root | `npx openapi-typescript ../Wellsynthai/openapi.json` produces clean `lib/types.ts` | Open |
| **C-02** | Commit sanitized real `JobManifest` JSON to `docs/fixtures/job_manifest.json` | Frontend Zod schema validates the file | Open |
| **C-03** | Tighten CORS to env-driven allow-list (`CORS_ALLOW_ORIGINS`, default `http://localhost:3000`) | Browser preflight from `localhost:3000` succeeds with `credentials: 'include'` | Open |
| **C-04** | Add `Cache-Control: public, max-age=86400, immutable` to `/documents/{id}/pages/{n}/image` and `/crop` (replaces legacy `/ingest/{job_id}/...` paths) | DevTools network panel shows the header on second load (304 / from disk cache) | Open |
| **C-05** | Document `Authorization: Bearer` pass-through in OpenAPI description | Description on every route mentions header is accepted-but-ignored in v1 | Open |

### 2.2 With PR-1 (Auto-Summary)

| ID | Item | Acceptance | Status |
|---|---|---|---|
| **C-06** | `GET /documents/{job_id}/summary` returns 404 if missing (not 200-null) | Frontend can render empty state on `error.status === 404` | Open |
| **C-07** | Manifest exposes `pages_meta[]: { page_num, model_used, escalated, confidence? }` | Field present on every `completed` job after backfill | Open |
| **C-08** | Each `key_fact` gains `confidence: "high" \| "medium" \| "low"` | Frontend can color KeyFactsPanel chips by confidence | Open |
| **C-09** | Run `python -m app.tools.backfill_summaries` on demo deploy as part of PR-1 deploy script | Existing 22-page WCR returns a non-null summary post-merge | Open |

### 2.3 With PR-2 (Compare)

| ID | Item | Acceptance | Status |
|---|---|---|---|
| **C-10** | `POST /compare` returns 409 when either job is not `completed` | Frontend grays out Compare CTA | Open |
| **C-11** | Add `GET /ingest/{job_id}/tables/{table_id}.json` (raw table) | Frontend can stop converting markdown → CSV client-side | Open |
| **C-12** | Add keyset pagination to `GET /ingest`: `?limit=50&before=<job_id>` | Default unchanged; opt-in via param | Open |

### 2.4 With PR-3 (Briefing) + PR-4 (Lessons)

| ID | Item | Acceptance | Status |
|---|---|---|---|
| **C-13** | `GET /entities/distinct?type={formation\|field\|operation\|section\|well_name}&q=<prefix>&limit=20` (B-008) | Briefing form autocomplete and Geologist prevalence chart bind to it | Open |
| **C-14** | `/chat` request gains `job_ids: string[] \| null` (strict superset; existing `job_id` and `doc_name` still work) | Pinboard "Chat with this board" can pass multi-doc scope | Open |
| **C-15** | `POST /briefings/pre-spud` accepts and echoes `owner: string` (B-010); `GET /briefings?owner=...` filter | "My briefings" block on Drilling Engineer dashboard works | Open |
| **C-16** | Lock `applicability.reasons` enum to exactly: `formation_match \| operation_match \| depth_band_match \| section_match` | Frontend can localize without runtime fallback | Open |
| **C-17** | Lock `empty_state_reason` enums (see [§3.7](#37-locked-enums)) | Frontend can localize without runtime fallback | Open |
| **C-18** | Briefing latency: target p50 < 15 s, p95 < 35 s | Telemetry confirms after PR-3 deploy | Open |

### 2.5 Pre-demo (P5 timing)

| ID | Item | Acceptance | Status |
|---|---|---|---|
| **C-19** | `POST /demo/seed-sample` (env-gated admin route) — re-ingest bundled `GSWA_Harvey_1_WCR.pdf` with deterministic `job_id = sha256(filename)[:16]` (B-004) | Onboarding step 2 (FRONTEND_SPEC §34) lands on the same job_id every time | Open |
| **C-20** | `GET /admin/stats` (B-007) — counts, spend, sparkline, model breakdown, 30 s cache | Super Admin dashboard renders without N+1 fanout | Open |
| **C-21** | `GET /admin/stats/sparkline?metric=spend\|jobs&days=7` — sparkline series detached from main payload | Sparkline block on Super Admin dashboard | Open |
| **C-22** | `GET /health` confirmed unauthenticated, returns `{status: "ok"}` (already exists) | Frontend uptime checks pass | Done (already in [main.py](../app/main.py)) |

### 2.6 Deferred (post-demo, listed for roadmap)

| ID | Item | Reason for defer |
|---|---|---|
| **C-23** | `GET /admin/events` audit stream (B-011) | Out of v1 scope; needs an event sink we don't have |
| **C-24** | Real RBAC | Phase 3 in `PLAN.md`, unchanged |
| **C-25** | Multi-value filters on `GET /search` (e.g. `kind in [text,table]`) | Only if perf hurts; client fanout fine for demo |
| **C-26** | SSE / WebSocket streaming for `/chat` | Typewriter on client suffices for v1 |
| **C-27** | Server-side `/chat/sessions/{id}` history | Sessions live client-side in v1 — **revisited under [§9.5](#95-chat-session-ownership-contradiction)** |
| **C-28** | Webhook for ingestion completion → email digest | Frontend handles email via Resend; no backend hook needed |

### 2.7 PR-5 — Supabase Auth + Postgres baseline ✅ **Done (2026-05-06)**

**Replaces the v1.1 plan.** No more passlib / itsdangerous / handwritten `/auth/signup` etc. Frontend talks to Supabase Auth directly via `@supabase/supabase-js`; backend verifies the resulting JWT.

**Verification:** `python -m app.tools.e2e_auth` exercises the full flow against the live API — admin-creates a user via service role, asserts the `handle_new_user` trigger inserted into `users_profile` (default role `drilling_engineer`), promotes to `super_admin`, signs in with anon key, calls `GET /auth/me` (200, payload matches), confirms 401 without token, deletes the test user. All green.

| ID | Item | Acceptance | Status |
|---|---|---|---|
| **C-29** | Add deps: `supabase`, `asyncpg`, `sqlalchemy>=2.0`, `pyjwt[crypto]`. **Approved by product owner 2026-05-06.** Drop earlier `passlib` / `itsdangerous` / `python-jose` from the plan. | `requirements.txt` updated; lock regenerated | ✅ Done |
| **C-30** | Supabase project provisioned (one project, two environments: `internal-staging`, `internal-prod`). Connection settings via env: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWKS_URL`, `DATABASE_URL` (asyncpg via Session Pooler). | Backend can read/write Postgres and verify JWTs | ✅ Done (staging) |
| **C-31** | `users_profile` table linked 1–1 to `auth.users.id` (Supabase-managed). Holds `role` (locked enum), `display_name`, `created_at`. RLS policy: user can read own row; super_admin can read all. | `INSERT INTO users_profile` on Supabase Auth `signup` event via DB trigger | ✅ Done — `supabase/migrations/0001_users_profile.sql`, trigger `handle_new_user` fires on `auth.users` insert |
| **C-32** | Supabase JWT verification middleware. Verifies **ES256** access tokens against the project JWKS (`SUPABASE_JWKS_URL`); JWKS cached in-process and refreshed on `kid` miss. Populates `request.state.user = { id, email, role }`. Anonymous routes (`/health`, `/openapi.json`) bypass. | Frontend call from `localhost:3000` with Supabase JWT succeeds; tampered JWT → 401 | ✅ Done — 60s leeway for clock skew; anon-bypass list locked at `/`, `/health`, `/openapi.json`, `/docs`, `/redoc`, `/docs/oauth2-redirect` |
| **C-33** | `GET /auth/me` returns `{ id, email, role, display_name }` joined from `auth.users` + `users_profile`. p95 < 50 ms (single SQL). | Frontend `useAuthStore` hydrates from this on every page load | ✅ Done |
| **C-34** | **No** `/auth/signup`, `/auth/login`, `/auth/logout`, `PATCH /auth/me/role` on backend. Frontend uses Supabase JS SDK directly. Role changes via `PATCH /auth/me/profile` (display_name only) — role mutations require super_admin via `PATCH /admin/users/{id}/role`. | Endpoint surface matches OpenAPI; Supabase SDK is the only sign-up path | ✅ Done (admin role-mutation endpoint deferred — see [§11.5](#115-still-open--what-the-backend-will-add-when-asked)) |
| **C-35** | Drop the existing `app/static/chat.html` and `app/static/ingest.html`. Frontend has its own; maintaining two auth paths is wasted work. | Files removed; `/ui` and `/ingest-ui` routes deleted from `app/main.py` | ✅ Done |

### 2.7.1 PR-5.1 — User management endpoints ✅ **Done (2026-05-06)**

**Why this exists:** product chose **open sign-up** for the internal-clients launch (no invite-only gate, no Dashboard juggling). Frontend builds Settings + Admin pages; backend provides the ops the Supabase JS SDK can't do (anything that needs the service-role key) plus the writable profile path.

**Verification:** `python -m app.tools.e2e_admin` — admin-creates a `super_admin`, signs in, exercises every endpoint (profile update, invite, list, role change, 403 on non-admin, delete), and cleans up. Green.

| ID | Endpoint | Auth | Notes |
|---|---|---|---|
| **C-49** | `PATCH /auth/me/profile` | self | Body `{ "display_name": string \| null }`. Returns the updated profile. |
| **C-50** | `GET /admin/users?q=&role=&limit=&offset=` | super_admin | Paginated. Filters: substring `q` on email/display_name; exact `role`. Returns `{items,total,limit,offset}`. |
| **C-51** | `PATCH /admin/users/{id}/role` | super_admin | Body `{ "role": "<one of locked enum>" }`. Returns the updated row. 400 on invalid role; 404 on missing user. |
| **C-52** | `POST /admin/users/invite` | super_admin | Body `{ email, display_name?, role? }`. Wraps `auth.admin.invite_user_by_email`. 201 with the new profile row. **Email transport:** Supabase Auth SMTP is pointed at **Resend** (custom SMTP), so invite / confirm / reset / magic-link mails all flow through Resend. Ceiling is whatever's set under Supabase → Auth → Rate Limits (default 100/hr). |
| **C-53** | `DELETE /admin/users/{id}` | super_admin | Cascades `users_profile` via FK. 204 on success. |
| **C-54** | `require_role(*roles)` dependency | — | New helper at [app/auth/deps.py](../app/auth/deps.py). Returns 403 with `{error_code: "requires_role", required_roles, actual_role}`. |

All admin routes return 403 (`requires_role`) when the caller is not super_admin — the frontend can hide the Admin tab on `auth/me.role !== 'super_admin'` and the backend enforces the same rule independently.

### 2.8 PR-6 — Storage migration (filesystem → Supabase Storage)

Moves all per-document artifacts off `data/output/<doc>/` and into Supabase Storage buckets. This is what makes ownership-at-rest real — buckets get RLS policies that the filesystem can never enforce.

| ID | Item | Acceptance | Status |
|---|---|---|---|
| **C-36** | Provision three Supabase Storage buckets: `pdfs` (originals), `pages` (rendered PNGs), `artifacts` (markdown + tables + entities JSON). All private; access via signed URLs only. | `supabase storage` CLI shows three buckets with private ACL | ✅ Done (2026-05-06) |
| **C-37** | Bucket RLS policies: owner-scoped read/write/delete on path[1] = `auth.uid()`. Path convention: `<bucket>/<owner_id>/<doc_id>/<filename>`. (Super-admin scope-all is **not** an RLS policy — backend admin ops use the service-role key, which bypasses RLS; an RLS subquery against `users_profile` from the storage role context errors out.) | Two test users — each can read own paths only; intruder gets `StorageApiError` | ✅ Done (2026-05-06) |
| **C-38** | Replace `app/storage/file_storage.py` with `app/storage/supabase_storage.py`. Same logical surface (`write_pdf`, `write_page_png`, `read_*`, `delete_doc`, …) but writes/reads go to buckets. `file_storage.py` deleted. | Existing tests pass with bucket backend | ✅ Done (2026-05-06) |
| **C-39** | Image-serving route `/ingest/{job_id}/pages/{n}/image` returns 302 → short-TTL Supabase signed URL (default 60 s, configurable via `SIGNED_URL_TTL_SECONDS`). `/crop` continues to proxy because it dynamically slices. | DevTools shows 302 to Supabase URL; PNG renders | ✅ Done (2026-05-06) |
| **C-40** | Worker (`app/workers/tasks/ingest.py`) downloads PDF from `pdfs` bucket into a temp file; orchestrator + postprocessor write all artifacts to buckets via `SupabaseStorage`. **No dual-write.** `OUTPUT_DIR` removed from config; `data/output/` no longer mutated by new ingestions. | E2E (`python -m app.tools.e2e_storage`) passes 7/7 incl. RLS denial | ✅ Done (2026-05-06) |

#### 2.8.1 PR-6 — what frontend needs to know

- **`POST /ingest` and `POST /ingest/upload` now require auth.** Send `Authorization: Bearer <supabase_jwt>` (the same token already used for `/auth/me`, `/admin/*`). Without it: `401 missing_token`. The PDF is stored under the authenticated user's `owner_id`.
- **`GET /ingest/{job_id}/pages/{n}/image` now 302-redirects** to a short-TTL Supabase signed URL (default 60 s). `axios` and `fetch` follow redirects automatically; existing `<img src=…>` bindings keep working with no change. If you cache the URL, refetch when it 4xx's — the signature has expired.
- **`GET /ingest/{job_id}/pages/{n}/image/crop`** continues to return image bytes directly (it dynamically crops a region; not a redirect).
- **`GET /ingest/{job_id}/full` and `/ingest/{job_id}/manifest`** unchanged in shape; still require ownership (or `super_admin`). 403 `Not authorized for this job` otherwise.
- **No FE config changes.** The bucket layout is invisible to the browser; everything still flows through the FastAPI routes.

### 2.9 PR-7 — Persistent documents + ownership + Realtime ✅ **Done (2026-05-06)**

Shipped. Identity moved to `(user_id, project_id, document_id)`; Qdrant wiped + recreated with tenant payload indices; Postgres `projects` + `documents` + `jobs` are the source of truth; Realtime publication on `jobs` is enabled. The shape diverged from the original C-41..C-48 plan in three ways worth calling out:

| Original plan | Actual shipped | Why |
|---|---|---|
| `content_hash` unique on `documents` so re-ingest reuses `documents.id` | Per-upload `documents.id` (uuid v4); user explicitly deletes + re-uploads | Avoids ambiguous "merge or replace?" UX; `DELETE /documents/{id}` is the explicit re-ingest path |
| `?scope=mine\|all` on every list endpoint | All list endpoints scope to caller automatically; super_admin sees all on individual GET | Fewer params, fewer footguns |
| Backfill CLI for legacy `data/output/` corpora | **Not shipped — wiped instead.** `python -m app.tools.qdrant_reset --yes` + re-ingest | User-approved: "we can wipe the qdrant and reingest as needed" |
| `briefings`, `lessons`, `pinboards` tables | **Deferred** to PR-3 / PR-4 (will pick up the tenant-block convention then) | Out of scope for the ingest-then-chat MVP |

| ID | Item | Status |
|---|---|---|
| **C-41** | `documents` table with `(id, user_id, project_id, filename, ...)` + RLS | ✅ |
| **C-42** | `jobs` table; `GET /ingest` and `GET /ingest/{job_id}` read from DB | ✅ |
| **C-43** | `projects` table + `/projects` CRUD + `organization` on `users_profile` | ✅ |
| **C-44** | Qdrant payload `{ user_id, role, organization, project_id, document_id }` + payload indices on every tenant field across all 3 collections | ✅ |
| **C-45** | Implicit owner-scoping on every list endpoint; super_admin sees all on `GET /ingest/{id}` and `GET /documents/{id}` | ✅ |
| **C-46** | ~~Backfill CLI~~ → wipe-and-reingest via `app.tools.qdrant_reset` | ✅ (substituted) |
| **C-47** | `jobs` table added to `supabase_realtime` publication; backend writes `queued → running → succeeded\|failed` transitions in the worker | ✅ |
| **C-48** | `PATCH /documents/{id}/visibility` reserved | Deferred (no `visibility` column in v1.0; private-only) |

**Wiping Qdrant:** the migration is destructive at the vector layer. Run once on each environment after pulling this PR:

```bash
python -m app.tools.qdrant_reset --yes
```

This drops and recreates `wellsynthai_documents`, `wellsynthai_entities`, `wellsynthai_images` with the tenant payload indices. Existing Postgres rows are left alone — you'll need to delete + re-ingest documents you want vector-searchable again.

---

## 3. Detailed Answers to Open Items

### 3.1 OpenAPI, CORS, Auth (B-001, B-002, B-003)

- **OpenAPI export:** `GET /openapi.json` is live (FastAPI default). C-01 commits a versioned snapshot to repo root so frontend can run `openapi-typescript` against a stable file.
- **CORS:** current state is broken (`*` + credentials). C-03 fixes it. Default dev allow-list: `["http://localhost:3000"]`. Vercel preview pattern matched via regex middleware: `https://*.vercel.app`. Production allow-list locked in P6.
- **Auth pass-through:** confirmed. Backend has zero auth middleware in v1. Any `Authorization: Bearer <jwt>` header is silently accepted. Documented per route in C-05.

### 3.2 Sample fixture corpus (B-004)

- `data/output/GSWA_Harvey_1_WCR/` exists with all artifacts.
- **Job_id is NOT stable today.** It's a UUID generated per ingest.
- **Resolution:** C-19 adds `POST /demo/seed-sample` with deterministic `job_id = sha256(filename)[:16]`. Frontend gets a guaranteed stable id for onboarding step 2.
- Will land before P5 (onboarding phase). If frontend wants it sooner, flag and we'll bring it forward.

### 3.3 JobManifest stability (B-005)

- Shape matches BACKEND_SPEC §1 and FRONTEND_SPEC §7.2.
- **Caveats to lock in:**
  - `tokens` and `cost_usd` are present on `completed` jobs.
  - On `processing` jobs they're partial (only what's accumulated). Never `null` once `pages_processed > 0`.
  - Initial `pending` state has `tokens: 0` and `cost_usd: 0.0`.
- C-02 commits a sanitized real manifest as the canonical fixture.

### 3.4 `doc_name` is NOT deterministic ⚠️ — **superseded by PR-7**

> **Resolved (2026-05-06).** PR-7 introduces a durable `documents.id` (uuid) which **is** stable across re-ingestions and is the correct grouping key for portfolio / dedupe / prevalence views. `doc_name` is now display-only metadata; `filename` remains the human label.

Historical context (kept for reference):

- `doc_name` is computed as `f"{safe_stem}_{job_id[:8]}"` so concurrent ingestions don't collide.
- **Frontend should group by `document_id`** going forward. `filename` remains a fine fallback for purely cosmetic grouping.

### 3.5 Page image URL semantics (B-006)

- **Path is now `/documents/{document_id}/pages/{n}/image`** (was `/ingest/{job_id}/...`; see §0.3).
- Returns a `302` redirect to a short-TTL Supabase signed URL. Browsers + axios + fetch follow automatically.
- Crop variant `/documents/{document_id}/pages/{n}/image/crop?ymin&xmin&ymax&xmax` returns image bytes directly (no redirect).
- Out-of-range `n` (≥ `total_pages`) → **404**.
- Bbox out of `0..1000` range → **400** with detail.

### 3.6 Manifest fields & enums

| Field | Locked values | Notes |
|---|---|---|
| `status` | `pending \| processing \| completed \| failed` | Top-level job status |
| `current_phase` | `extraction \| validation \| escalation \| postprocessing \| indexing` | Only set while `status == processing` |
| `pages_meta[].model_used` | `flash \| pro` | Added in C-07 |
| `pages_meta[].escalated` | `boolean` | True if Pro escalation kicked in |
| `pages_meta[].confidence` | `"high" \| "medium" \| "low"` | Optional in v1; populated when validator emits a confidence |

### 3.7 Locked enums

**Briefing `empty_state_reason`:**
- `"no_offsets_in_field"`
- `"no_formation_overlap"`
- `"corpus_too_small"`

**Lessons `empty_state_reason`:**
- `"no_lesson_keywords_in_corpus"`
- `"no_classified_lessons"`
- `"context_too_vague"`

**Lessons `applicability.reasons[]`:**
- `"formation_match"`
- `"operation_match"`
- `"depth_band_match"`
- `"section_match"`

Frontend can localize these directly. Backend will not introduce new values without bumping the OpenAPI minor version and notifying via this doc.

### 3.8 `/ingest` pagination

- Today: returns up to 100 manifests, no pagination.
- C-12 adds keyset pagination: `?limit=50&before=<job_id>`. Default unchanged; opt-in.
- For demo (≤ 50 jobs) frontend can keep the unparameterized call.

### 3.9 ETA / `current_phase` / `cost_usd` confirmations

- **ETA:** backend does NOT compute. Client-side `pages_processed / elapsed` is fine. Will not add.
- **`current_phase` enum stability:** locked per [§3.6](#36-manifest-fields--enums).
- **`cost_usd` reliability:** non-null on every `completed` job. Confirmed.

### 3.10 Chat (`/chat`) clarifications

| Question | Answer |
|---|---|
| `attached_pages[]` URL absolute or relative? | **Relative** (e.g. `/ingest/{job_id}/pages/4/image`). Frontend prefixes with `NEXT_PUBLIC_API_URL`. |
| Streaming (SSE)? | **No** — request/response only for v1. Typewriter on the client. |
| `session_id` lifetime? | Client-issued UUID, **not persisted server-side**. `/chat/sessions/{id}` is NOT a future ask in v1. |
| Cross-doc support? | C-14 adds `job_ids: string[] \| null` to the request schema in PR-3. Removes the FRONTEND_SPEC §28 v1 limitation cleanly. |

### 3.11 Compare (`POST /compare`)

- Response shape locked to BACKEND_SPEC §6.2.
- 409 when either job is not `completed`. Confirmed.
- Lands in PR-2 (after PR-1 Auto-Summary).

### 3.12 Tables raw endpoint

- **Today:** tables are persisted to disk under `data/output/{doc_dir}/tables/{table_id}.json` but no HTTP endpoint serves them.
- C-11 adds `GET /ingest/{job_id}/tables/{table_id}.json`. ~15 lines. Lands with PR-2.

### 3.13 Search filters

- Multi-value filters (e.g. `kind in [text,table]`): **not in v1**. Client fanout is acceptable.
- C-25 deferred unless perf hurts.

---

## 4. Per-Phase Commentary

### Phase 0 — Foundations
No changes. C-01 through C-05 unblock all P0 work.

### Phase 1a — Shell + Ingest + Library + Role Dashboards
- All `/ingest/*` and `/upload` endpoints exist. No backend blockers.
- Visual refinement (cooler tokens, JetBrains Mono numerals, density default → compact, no card-top gradients) is endorsed. No backend impact.
- B-007 (`/admin/stats`) deferred to pre-demo (C-20). For P1a, Super Admin dashboard does client-side aggregation over `GET /ingest` — acceptable for ≤ 100 jobs.

### Phase 1b — Document Workspace + Chat + Auto-Summary
- PR-1 lands here. C-06 / C-07 / C-08 / C-09 all in scope.
- 404 for missing summary (frontend preference) confirmed.
- Backfill CLI runs as part of PR-1 deploy.

### Phase 2 — Browsers + Search + Compare
- PR-2 lands here. C-10 / C-11 / C-12 all in scope.
- Tables CSV: frontend uses C-11 raw JSON; markdown→CSV client conversion can be removed.

### Phase 3 — Killer Features + Pinboard
- PR-3 (Briefing) and PR-4 (Lessons) land here. C-13 / C-14 / C-15 / C-16 / C-17 / C-18 all in scope.
- B-008 (`/entities/distinct`) lands alongside PR-3 — unblocks Briefing form autocomplete AND Geologist prevalence chart in one shot.
- `job_ids[]` superset on `/chat` removes the §28 v1 limitation. No "system prefix" workaround needed.

### Phase 4 — Reports / Export / Email / Sharing
- Zero backend impact. Resend, share tokens, PDF export are all frontend-owned.
- C-28 (webhook for ingestion-complete email) deferred.

### Phase 5 — Premium Polish
- C-19 (seed-sample) and C-20 / C-21 (`/admin/stats`) land here.
- `GET /health` already exists (C-22 done).

### Phase 6 — Hardening & Launch
- Production CORS allow-list locked here.
- Rate limits: none planned for v1; demo-scale traffic only. Flag if load testing shows otherwise.
- Failure modes: every endpoint returns RFC 7807 problem JSON on 4xx/5xx. Will document the full list before P6.

---

## 5. Roles & Dashboards — Endorsement & Notes

### 5.1 Endorsed in full
- **5 v1 roles**, default `drilling_engineer`. Correct.
- **Role as frontend `localStorage` preference** in v1; backend RBAC deferred. Correct.
- **Build order:** Super Admin + Drilling Engineer in P1a; Asset Manager in P4; Geologist + QA Steward in P5. Correct.
- **Single shell, role-resolved primary view** with block library architecture. Correct.
- **First-run heuristic** (inline chip prompt, not a wizard). Correct.
- **§5 visual refinement** (token tweaks, JetBrains Mono, density default, no card gradients, hairline borders, charts strokes-only). Correct.

### 5.2 Caveats

**Geologist "Formation prevalence" block:**
- Without L2 entity resolution, `"Eagle Ford"`, `"Eagle Ford Fm."`, and `"EAGLE FORD"` may appear as three separate `canonical_value`s.
- **Recommendation for v1:** frontend does display-side normalization (uppercase + strip `"Fm."` / `"Formation"` suffixes).
- L2 entity resolution remains in `PLAN.md`; once it lands, the heuristic can be removed.

**QA Steward "Pages that escalated" block:**
- Only useful once `pages_meta[]` is populated (C-07).
- Backfill needed for older jobs. C-09 covers this.
- Dashboard ships best in P5 once a few real ingestions populate the view.

### 5.3 Suggestion (non-blocking)

Add **Wellsite Supervisor / Operations Engineer** to the post-demo roadmap. Drilling Engineer answers *"What should I worry about on the next well?"* (planning). Wellsite Supervisor answers *"What's happening on the well I'm drilling right now?"* (live ops). The L6 synthesis agents (Daily Summary, Shift Handover) are this persona's natural home.

For v1: defer (no DDR cadence, no shift handover ingestion). For roadmap: keep visible.

---

## 6. What Backend Wants From Frontend

1. **Sign off on §2 commitment list** — confirm acceptance and target windows so it becomes the contract. ✅ Done 2026-05-06.
2. **Confirm doc_name grouping mental model** ([§3.4](#34-doc_name-is-not-deterministic)) — frontend will group cross-ingest aggregations by `filename`, not `doc_name`. ✅ Confirmed.
3. **Lock `applicability.reasons` enum** to the four values in [§3.7](#37-locked-enums) before P3 starts. ✅ Confirmed.
4. **Lock `empty_state_reason` enums** before P3 starts. ✅ Confirmed.
5. **Reference the existing static UI** ([app/static/chat.html](../app/static/chat.html), [app/static/ingest.html](../app/static/ingest.html)) as a working reference for ambiguous API behavior. Not a design reference — a behavioral one.
6. **Flag any of C-19 / C-20 / C-21** if needed earlier than P5. They're easy to bring forward.
7. **NEW (B-012/B-013):** answer the six clarifications in [§9.7](#97-clarifications-needed-from-frontend) before we open the auth PR. Most are 1-line answers.

---

## 7. Communication Cadence

- **This doc updates** at every backend PR merge (C-01 → C-22 status flips Open → Done).
- **Per-PR snapshot:** when a PR merges, this doc is updated AND a fresh `openapi.json` is committed AND a note is added to BACKEND_REPORT in the relevant phase's "Resolved" section.
- **Weekly sync:** backend brings updated commitment list with status; frontend brings any new asks for the next phase.
- **Phase boundary:** both sides update their respective living docs (BACKEND_REPORT for frontend asks, this doc for backend commitments).

---

## 8. Glossary

| Term | Meaning |
|---|---|
| **B-NNN** | Backend ask, originated by frontend in BACKEND_REPORT.md |
| **C-NN** | Backend commitment, tracked in this doc's §2 |
| **PR-N** | Backend PR per BACKEND_SPEC §9 (PR-1 Summary, PR-2 Compare, PR-3 Briefing, PR-4 Lessons) |
| **MOD-N** | Frontend modification to the spec milestones, per IMPLEMENTATION_PLAN §1 |
| **W-N** | Frontend work item per ROLES_AND_DASHBOARDS §6.2 |

---

## 9. Response to B-012 + B-013 — Auth + Ownership (Supabase-native)

This section was rewritten on 2026-05-06 after the product decision to adopt **Supabase as the platform of record** for Postgres + Auth + Storage + Realtime. The original v1.1 plan (passlib + itsdangerous + custom `/auth/signup`) is dropped.

### 9.1 Why Supabase

- **One vendor for four needs.** Postgres (durable state), Auth (JWTs + invitations + email verification), Storage buckets (per-owner object storage with RLS), Realtime (replaces ingest polling). All on a single dashboard with a single billing relationship.
- **RLS at the storage layer** — a filesystem under `data/output/` cannot enforce "user A cannot read user B's PDFs." Supabase bucket policies can.
- **No demo posture.** Internal-clients-first means real onboarding via Supabase Auth invitations, real signed-URL serving, real Realtime.
- **Auth surface deletion.** We don't ship `/auth/signup`, `/auth/login`, `/auth/logout`, password reset, or email verification — Supabase Auth handles all of it. Backend's only auth duty is JWT verification middleware.

### 9.2 Dependencies — approved

Product owner approved 2026-05-06. Final list (replaces v1.1):

| Package | Why |
|---|---|
| `supabase` | Python client for Storage, Realtime broadcast, admin DB ops |
| `asyncpg` | Direct async Postgres driver (performance-critical path; bypasses PostgREST) |
| `sqlalchemy>=2.0` | ORM for `documents`, `jobs`, `briefings`, `lessons`, `pinboards`, `users_profile` |
| `pyjwt[crypto]` | JWT verification of Supabase-issued access tokens (**ES256** verified against the project JWKS) |

Dropped from the v1.1 list: `passlib[bcrypt]`, `python-jose`, `itsdangerous`, `alembic` (Supabase CLI handles migrations).

### 9.3 Document persistence model (resolves the `doc_name` problem)

The `f"{safe_stem}_{job_id[:8]}"` convention was always going to be a frontend headache. The Supabase pivot lets us replace it cleanly:

```text
documents
  id              uuid pk          -- stable across re-ingestion
  owner_id        uuid → auth.users
  filename        text             -- display name
  content_hash    sha256(bytes)    -- de-duplication key
  total_pages     int
  visibility      text             -- private | org
  current_job_id  uuid → jobs      -- latest ingestion run
  created_at      timestamptz
  UNIQUE (owner_id, content_hash)

jobs
  id              uuid pk          -- aka job_id (per-run only)
  document_id     uuid → documents
  owner_id        uuid → auth.users
  status          text             -- pending | processing | completed | failed
  current_phase   text             -- extraction | validation | escalation | postprocessing | indexing
  pages_processed int
  total_pages     int
  tokens_in       int
  tokens_out      int
  cost_usd        numeric(10,4)
  error           text null
  started_at      timestamptz
  completed_at    timestamptz null
```

**Implications for frontend:**

- **`doc_name` is dead.** Use `documents.id` (uuid) as the stable identifier and `documents.filename` for display. The earlier ask to "group by `filename`" ([§3.4](#34-doc_name-is-not-deterministic)) is superseded — group by `documents.id`.
- **Re-ingestion is non-destructive.** Uploading the same PDF (same `content_hash`) keeps the existing `documents.id`, attaches a new `job_id`, replaces artifacts in Storage, and reindexes Qdrant points (delete-by-`document_id` then re-insert). Pinboard pins, briefings, and lessons keyed to `documents.id` survive.
- **URL shape stabilises.** All resource URLs become `/documents/{document_id}/...` instead of `/ingest/{job_id}/...`. The `/ingest/*` URLs stay as the **ingestion lifecycle** namespace (upload, watch progress); `/documents/*` is the **content** namespace (pages, tables, entities, summary, briefings). [§10.3](#103-revised-url-namespace) lists the new shapes.

### 9.4 Frontend auth integration (the only frontend change)

Frontend uses `@supabase/supabase-js` directly. Backend never sees a password.

```ts
// Sign in
const { data, error } = await supabase.auth.signInWithPassword({
  email, password,
})
// data.session.access_token is a Supabase JWT

// Attach to every API call
fetch(`${NEXT_PUBLIC_API_URL}/documents`, {
  headers: { Authorization: `Bearer ${session.access_token}` }
})
```

Backend's middleware verifies the JWT against the project's JWKS (`SUPABASE_JWKS_URL`, ES256 / P-256), looks up `users_profile.role` (cached per-request), and exposes `request.state.user`.

**No backend `/auth/signup` or `/auth/login`.** The frontend's `iron-session` plan in IMPLEMENTATION_PLAN §3 P0 should be replaced with Supabase Auth's built-in session handling (cookies in browser, refresh tokens, JWT issuance). Frontend already added `@supabase/supabase-js` is the recommendation.

### 9.5 Realtime — polling dies

The IMPLEMENTATION_PLAN P1a uses `refetchInterval: 2000` while jobs are `processing`. Replace with a Supabase Realtime subscription:

```ts
const sub = supabase
  .channel(`jobs:owner_id=eq.${user.id}`)
  .on('postgres_changes',
      { event: 'UPDATE', schema: 'public', table: 'jobs' },
      (payload) => updateJobInStore(payload.new))
  .subscribe()
```

Backend writes `jobs` row updates inside the orchestrator on every phase transition; Realtime fans out automatically. `GET /ingest` polling stays as a fallback (e.g., for tools / curl) but the UI doesn't use it.

### 9.6 Storage migration — `data/output/` is legacy

All new ingestions write artifacts to Supabase Storage:

| Bucket | Path convention | Contents |
|---|---|---|
| `pdfs` | `<owner_id>/<document_id>/original.pdf` | Original upload (immutable) |
| `pages` | `<owner_id>/<document_id>/page_{NNN}.png` | Rendered page PNGs |
| `artifacts` | `<owner_id>/<document_id>/page_{NNN}.md`, `tables/<id>.{md,json}`, `entities.json`, `full_document.md` | Extracted artifacts |

Bucket RLS: read-own + read-org-visible + super_admin-all. Image-serving endpoints redirect to short-TTL signed URLs (60 s default). The bbox `/crop` endpoint stays as a proxy because it slices on the fly.

### 9.7 What we want from frontend (revised)

The v1.1 "6 clarifications" are mostly moot under Supabase. The remaining decisions:

| # | Question | Recommended answer |
|---|---|---|
| **Q-1** | Confirm frontend will use `@supabase/supabase-js` for auth (sign-in, sign-up, session refresh) and stop planning `iron-session` / custom `/api/auth/*` routes. | Yes — Supabase SDK end-to-end. |
| **Q-2** | Confirm frontend will subscribe to Realtime channels for ingest progress instead of polling `GET /ingest/{job_id}`. | Yes for the UI. Polling stays available as a fallback. |
| **Q-3** | Confirm URL shape change: `/documents/{document_id}/...` for content; `/ingest/{job_id}/...` for ingestion lifecycle only. ([§10.3](#103-revised-url-namespace)) | Confirm before PR-7 lands. |
| **Q-4** | Internal client onboarding flow — Supabase Auth invitations (we send invite emails, user sets password on first login) or self-signup with email verification? | Invitations for v1. Self-signup deferred. |
| **Q-5** | `super_admin` role assignment — set via Supabase dashboard manually for v1, or build a `PATCH /admin/users/{id}/role` endpoint? | Manual via dashboard for v1. Endpoint when we have > 5 admins. |

### 9.8 What lands on the backend side (revised file map)

**New files:**

- `app/auth/middleware.py` — Supabase JWT verification, populates `request.state.user`.
- `app/auth/routes.py` — `GET /auth/me` only. No signup/login.
- `app/db/engine.py` — async SQLAlchemy engine over `DATABASE_URL` (Supabase Postgres).
- `app/db/models.py` — `UserProfile`, `Document`, `Job`, `Briefing`, `Lesson`, `Pinboard`.
- `app/storage/supabase_storage.py` — replaces `app/storage/file_storage.py`.
- `app/realtime/jobs_publisher.py` — thin helper writing job-state transitions to Postgres so Realtime picks them up.
- `app/tools/backfill_supabase.py` — one-time migration of `data/output/` + Qdrant payloads to Supabase.
- `supabase/migrations/0001_users_profile.sql`, `0002_documents_jobs.sql`, `0003_briefings_lessons_pinboards.sql`, `0004_storage_policies.sql`.

**Touched files:**

- [app/main.py](../app/main.py) — register auth middleware, drop `/ui` and `/ingest-ui` static routes.
- [app/routes/ingest.py](../app/routes/ingest.py) — stamp `owner_id`, write to `jobs` table, remove Redis-as-source-of-truth code paths.
- [app/routes/search.py](../app/routes/search.py) — visibility filter in Qdrant query.
- [app/routes/chat.py](../app/routes/chat.py) — enforce `owner_id`-bound scope; accept `job_ids[]` (already C-14, now `document_ids[]`).
- [app/storage/redis_store.py](../app/storage/redis_store.py) — reduce to Celery broker + per-job progress cache. Manifest persistence moves to Postgres.
- [app/vectorstore/qdrant_store.py](../app/vectorstore/qdrant_store.py) — payload indices, filter clauses.
- [app/vectorstore/embedder.py](../app/vectorstore/embedder.py), `entity_indexer.py`, `image_indexer.py` — write `owner_id` + `visibility` + `document_id` to payload.
- [app/workers/tasks/ingest.py](../app/workers/tasks/ingest.py), [app/pipeline/orchestrator.py](../app/pipeline/orchestrator.py) — propagate `owner_id` + `document_id`; write status to Postgres on each phase transition.
- [requirements.txt](../requirements.txt) — 4 new deps (C-29).
- **Deleted:** `app/static/chat.html`, `app/static/ingest.html` and their routes (C-35).

**Approximate LOC:** ~1500 new (auth + DB + storage + realtime), ~400 touched, ~600 deleted (filesystem helpers, static UI). Test coverage adds ~700 LOC.

### 9.9 Risk register (revised)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Supabase project provisioning slips (org-level approval) | Med | High | Provision `internal-staging` project today on a personal account; promote to org on green-light. |
| Vector-layer leakage during cutover | Med | High (privacy) | Backfill (C-46) writes `owner_id` to every Qdrant point before the scope filter is enforced. Smoke test asserts cross-user query returns 0 hits. |
| Storage migration loses artifacts | Low | High | Backfill is read-only on `data/output/`; legacy paths remain readable until `data/output/` is manually purged post-cutover. |
| Supabase Realtime delivery jitter | Low | Med | Polling endpoints stay alive as a fallback; frontend can re-enable on connection drop. |
| Direct asyncpg + Supabase connection limits (default 60 pooled) | Med | Med | Use Supabase's transaction pooler (port 6543) for ingestion worker; session pooler (5432) for FastAPI. |
| Re-ingestion races (same PDF uploaded twice in parallel by same user) | Low | Med | `UNIQUE(owner_id, content_hash)` on `documents` + `INSERT ... ON CONFLICT DO NOTHING` returning the existing row. |
| Filesystem `data/output/` left in inconsistent half-migrated state | Med | Low | Backfill is idempotent; can be re-run. Cutover deletes filesystem only after Storage write is confirmed. |

---

## 10. Migration plan summary (read this for the proceed order)

### 10.1 What ships first — PR-5 (this week) ✅ **Done**

1. ✅ Provision Supabase project (`internal-staging`).
2. ✅ Add 4 deps; backend boots with no DB usage yet.
3. ✅ Implement JWT verification middleware and `GET /auth/me`.
4. ✅ Create `users_profile` table + Supabase Auth signup trigger (DB function inserts default `users_profile` row on `auth.users` insert).
5. ⏳ Frontend wires `@supabase/supabase-js`, drops `iron-session`, can call `GET /auth/me` end-to-end. **← frontend action, see [§11](#11-user-creation--sign-up-flow-for-the-frontend-team).**
6. ✅ Delete `app/static/chat.html` + `ingest.html` + their routes.

Nothing else changes. Ingestion still writes to `data/output/`; manifests still live in Redis. **PR-5 was the smallest possible auth landing.** Smoke tooling: `python -m app.tools.check_supabase`, `python -m app.tools.apply_migrations`, `python -m app.tools.e2e_auth`.

### 10.2 What ships next — PR-6 + PR-7 (weeks 2–4)

In order: storage migration → documents/jobs schema → Qdrant payload migration → scope filtering → backfill → Realtime.

Frontend can integrate `/auth/me` against staging starting end of week 1. Real document/job calls start end of week 3.

### 10.3 Revised URL namespace

| Old | New | Notes |
|---|---|---|
| `GET /ingest/{job_id}` | `GET /jobs/{job_id}` | Per-run state |
| `GET /ingest/{job_id}/full` | `GET /documents/{document_id}/full` | Stable; survives re-ingest |
| `GET /ingest/{job_id}/pages/{n}/image` | `GET /documents/{document_id}/pages/{n}/image` | Redirects to signed URL |
| `GET /ingest/{job_id}/pages/{n}/image/crop` | `GET /documents/{document_id}/pages/{n}/image/crop` | Proxied (dynamic) |
| `POST /ingest` (start a job from existing PDF) | `POST /ingest` (unchanged) | Returns `{ job_id, document_id }` |
| `POST /ingest/upload` | `POST /ingest/upload` (unchanged) | Returns `{ job_id, document_id }`; document is upserted by `content_hash` |
| `GET /ingest` (list jobs) | `GET /jobs?scope=mine\|all` | Active + recent runs |
| *(new)* | `GET /documents?scope=mine\|all` | Library view — 1 row per document, latest job summary inline |
| *(new)* | `GET /documents/{id}/summary` | Auto-summary by document_id (was: by job_id) |

The old `/ingest/{job_id}/*` URLs stay alive as 308-redirects to the new `/documents/{document_id}/*` URLs for one release cycle, then deleted.

### 10.4 Frontend action items

1. Add `@supabase/supabase-js` to the Next.js app; drop the `iron-session` plan.
2. Read `NEXT_PUBLIC_SUPABASE_URL` + `NEXT_PUBLIC_SUPABASE_ANON_KEY` from env.
3. `useAuthStore` hydrates from `supabase.auth.getSession()` + `GET /auth/me` (for `role`).
4. Replace `refetchInterval: 2000` ingestion polling with a Realtime subscription on `jobs WHERE owner_id = me`.
5. Update `lib/types.ts` to use `documents.id` (string uuid) as the stable doc identifier; mark `job_id` as per-run only.
6. Update MSW handlers: jobs include `document_id`; document list endpoint added.
7. Confirm Q-1 → Q-5 in [§9.7](#97-what-we-want-from-frontend-revised).

---

## 11. User creation / sign-up flow (for the frontend team)

> **Audience:** the frontend engineer wiring auth into the Next.js app.
> **TL;DR:** there is no backend signup endpoint. Use `@supabase/supabase-js` against the project's anon key. Our `handle_new_user` trigger creates the `users_profile` row automatically; backend `/auth/me` returns it. **Verified live in PR-5 e2e.**

### 11.1 Sign-up

```ts
import { createClient } from '@supabase/supabase-js'

const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
)

const { data, error } = await supabase.auth.signUp({
  email,
  password,
  options: {
    data: { display_name: fullName },        // → raw_user_meta_data.display_name
    emailRedirectTo: `${window.location.origin}/auth/callback`,
  },
})
```

What happens server-side, automatically:

1. Supabase Auth creates the `auth.users` row.
2. Our `handle_new_user` trigger (`supabase/migrations/0001_users_profile.sql`) inserts the matching `public.users_profile` row with **default role `drilling_engineer`** and `display_name` taken from `raw_user_meta_data.display_name` (falls back to email if absent).
3. Supabase sends a confirmation email (default project setting). The user must click the link before sign-in works.
4. Once they sign in, `supabase-js` holds the JWT — attach it as `Authorization: Bearer <jwt>` to every backend request.

### 11.2 Sign-in / sign-out / refresh

```ts
// Sign in
await supabase.auth.signInWithPassword({ email, password })

// Get current session (auto-refreshes when needed)
const { data: { session } } = await supabase.auth.getSession()
const token = session?.access_token

// Sign out
await supabase.auth.signOut()
```

The SDK refreshes tokens transparently. Don't roll your own refresh; just read `session.access_token` whenever you need to call our API.

### 11.3 Hydrating `useAuthStore`

The JWT carries `id` + `email` + Supabase-internal `role` (always `"authenticated"`). The **app role** (`super_admin | drilling_engineer | geologist | asset_manager | qa_steward`) lives in `users_profile` and is only available via `GET /auth/me`:

```ts
const res = await fetch(`${API_BASE}/auth/me`, {
  headers: { Authorization: `Bearer ${session.access_token}` },
})
// 200 → { id, email, role, display_name }
// 401 → missing or invalid token (clear session, redirect to /login)
// 403 'no_profile' → user exists in auth but trigger didn't fire — file a bug
```

Call this once on every page load (or React Query with a long `staleTime`). Don't cache `role` past sign-out.

### 11.4 Defaults & gotchas

- **Default role is always `drilling_engineer`.** Promotions are an admin operation, not a signup field. We will not honor any role hint in `signUp({ data: { role: ... } })` — the trigger ignores it. Use `PATCH /admin/users/{id}/role` ([§2.7.1](#271-pr-51--user-management-endpoints--done-2026-05-06)) to promote.
- **Display name field name is locked to `display_name`.** If you need a different shape, ping us before changing the trigger. Self-service edits go through `PATCH /auth/me/profile`.
- **Email transport: Resend via Supabase custom SMTP.** All transactional mail (invite, confirm, password reset, magic link) is routed through Resend by configuring Supabase Auth → SMTP Settings with `smtp.resend.com:465`, user `resend`, password = a Resend API key. Sender domain must be verified in Resend. **No backend code touches Resend directly** — we keep `auth.admin.invite_user_by_email` and let Supabase deliver via the custom SMTP. Rate limit is whatever's set under Supabase → Auth → Rate Limits.
- **Email confirmations are ON by default** in the Supabase project. Until the user clicks the link, `signInWithPassword` returns `Email not confirmed`. Surface a useful message.
- **Open public sign-up is the chosen posture for internal clients v1.** Anyone with the anon key can call `signUp`; `users_profile` is created with default role `drilling_engineer`. Promotion / off-boarding lives entirely in the app via the admin endpoints — no Supabase Dashboard juggling.
- **Anon key is safe to ship to the browser.** It's rate-limited and scoped by RLS. Service-role key is **never** sent to the browser — backend-only.

### 11.5 Profile + Admin endpoints (shipped in PR-5.1)

Full spec lives in [§2.7.1](#271-pr-51--user-management-endpoints--done-2026-05-06). Quick reference for the FE:

```ts
// Self: update display name
await fetch(`${API_BASE}/auth/me/profile`, {
  method: 'PATCH',
  headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
  body: JSON.stringify({ display_name: 'Jane Doe' }),
})  // 200 -> { id, email, role, display_name }

// Admin: list users (super_admin only; 403 otherwise)
await fetch(`${API_BASE}/admin/users?q=jane&limit=50&offset=0`, {
  headers: { Authorization: `Bearer ${token}` },
})  // 200 -> { items: UserRow[], total, limit, offset }

// Admin: promote / demote
await fetch(`${API_BASE}/admin/users/${id}/role`, {
  method: 'PATCH',
  headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
  body: JSON.stringify({ role: 'geologist' }),
})

// Admin: invite by email (sends Supabase invite mail; 4/hr default rate limit)
await fetch(`${API_BASE}/admin/users/invite`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
  body: JSON.stringify({ email: 'new@example.com', display_name: 'New Hire', role: 'asset_manager' }),
})  // 201 -> UserRow

// Admin: off-board (cascades users_profile)
await fetch(`${API_BASE}/admin/users/${id}`, {
  method: 'DELETE',
  headers: { Authorization: `Bearer ${token}` },
})  // 204
```

**Error contract:** non-super_admin caller → `403 { error_code: "requires_role", required_roles, actual_role }`. Hide the Admin tab from non-admins; backend re-checks anyway.

### 11.6 Smoke recipes

```bash
# Auth flow (sign-up trigger + /auth/me)
python -m app.tools.e2e_auth

# Profile + admin endpoints (PR-5.1)
python -m app.tools.e2e_admin
```

Both scripts admin-create throwaway users, exercise the full path, and clean up. Run them from the backend repo with `.env` pointing at the target environment.

---

*End of Backend → Frontend Communication v2.3 — 2026-05-06 (PR-5 + PR-5.1 + PR-6 shipped; ingestion artifacts now in Supabase Storage; auth required on `/ingest*`; page-image route 302's to signed URLs).*
