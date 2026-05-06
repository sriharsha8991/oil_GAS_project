# WellSynth AI — Backend → Frontend Communication

> **Purpose:** Living document the **backend team** uses to respond to the frontend team. Mirrors [BACKEND_REPORT.md](BACKEND_REPORT.md) (which is frontend → backend). Forward-replace this doc whenever a backend commitment changes status.
>
> **How to read:** §1 is the executive summary. §2 is the canonical commitment list (versioned action items). §3 answers every open question raised in `BACKEND_REPORT.md`. §4 is per-phase commentary. §5 covers the role-aware dashboard endorsement. §6 lists what we want from frontend in return.
>
> **Last updated:** 2026-05-06 — **v2.1**. **PR-5 (Supabase Auth + Postgres baseline) shipped and verified end-to-end.** v2.0 architectural pivot retained: Supabase-native (Postgres + Auth + Storage + Realtime). Product stage: internal-clients enterprise.
> **Owner:** Backend team.
> **Status:** §2.7 PR-5 ✅ **Done** (C-29 … C-35 closed; live e2e: admin-create user → trigger → sign-in → `GET /auth/me` 200 → cleanup). §2.8 / §2.9 still Open. **§11 added** — user creation / sign-up flow for the frontend team. Frontend can now integrate `@supabase/supabase-js` against staging.

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
| **C-04** | Add `Cache-Control: public, max-age=86400, immutable` to `/ingest/{job_id}/pages/{n}/image` and `/crop` | DevTools network panel shows the header on second load (304 / from disk cache) | Open |
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

### 2.8 PR-6 — Storage migration (filesystem → Supabase Storage)

Moves all per-document artifacts off `data/output/<doc>/` and into Supabase Storage buckets. This is what makes ownership-at-rest real — buckets get RLS policies that the filesystem can never enforce.

| ID | Item | Acceptance | Status |
|---|---|---|---|
| **C-36** | Provision three Supabase Storage buckets: `pdfs` (originals), `pages` (rendered PNGs), `artifacts` (markdown + tables + entities JSON). All private; access via signed URLs only. | `supabase storage` CLI shows three buckets with private ACL | Open |
| **C-37** | Bucket RLS policies: read-own + read-org-visible + super_admin-all. Path convention: `<bucket>/<owner_id>/<document_id>/<filename>`. | Two test users — each can read own paths only; super_admin reads both | Open |
| **C-38** | Replace `app/storage/file_storage.py` with a Supabase Storage client wrapper. Same public interface (`save_page_png`, `save_markdown`, `save_table`, `read_*`) but writes/reads go to buckets. | Existing tests pass with bucket backend | Open |
| **C-39** | Image-serving routes (`/ingest/{job_id}/pages/{n}/image`, `/crop`) redirect to short-TTL signed URLs (default 60 s). Crop endpoint still proxies because it dynamically slices. | DevTools shows 302 to Supabase URL; PNG renders | Open |
| **C-40** | Worker tasks (`app/workers/tasks/ingest.py`) and orchestrator (`app/pipeline/orchestrator.py`) write artifacts to buckets via the new wrapper. **No dual-write.** Filesystem writes deleted in the same PR. | `data/output/` no longer mutated by new ingestions | Open |

### 2.9 PR-7 — Persistent documents + ownership + Realtime

The big one. Establishes durable document identity, per-user ownership, vector-layer isolation, and live progress via Realtime. Replaces v1.1's C-33→C-38.

| ID | Item | Acceptance | Status |
|---|---|---|---|
| **C-41** | `documents` table: `id` (uuid pk), `owner_id` (→ auth.users), `filename`, `content_hash` (sha256), `total_pages`, `visibility` (`private \| org`), `created_at`, `current_job_id` (→ jobs). Unique on `(owner_id, content_hash)` so re-ingesting the same PDF resolves to the same `documents.id`. | Re-uploading the same PDF returns the existing `documents.id`; new `job_id` only | Open |
| **C-42** | `jobs` table mirrors current `JobManifest` shape but as a row: `id` (=job_id), `document_id` (fk), `owner_id`, `status`, `current_phase`, `pages_processed`, `total_pages`, `tokens_in`, `tokens_out`, `cost_usd`, `started_at`, `completed_at`, `error`. RLS: own rows + super_admin scope-all. | `GET /ingest` reads from DB, not Redis | Open |
| **C-43** | `briefings`, `lessons`, `pinboards` tables — same `owner_id` + `visibility` shape. Lands ahead of PR-3 / PR-4 so those endpoints are born owner-aware. | Schema in `supabase/migrations/0003_*.sql` | Open |
| **C-44** | Qdrant payload: every point in `wellsynthai_chunks`, `wellsynthai_entities`, `wellsynthai_images` carries `owner_id` (uuid) + `visibility` + `document_id`. Payload index on `owner_id`. Retrieval filters in `app/vectorstore/qdrant_store.py` add `must` clause. | Cross-user search returns 0 hits (smoke test) | Open |
| **C-45** | `?scope=mine\|all` on every list endpoint (`/ingest`, `/briefings`, `/lessons`, `/search`, `/entities/distinct`). `all` requires `super_admin`; else 403 `requires_super_admin`. | Non-admin gets 403 with locked error code | Open |
| **C-46** | Backfill CLI `python -m app.tools.backfill_supabase` — imports legacy `data/output/<doc>/` artifacts into buckets, creates `documents` + `jobs` rows, updates Qdrant payloads with `owner_id`. Owner is configurable per-doc via CLI prompt for first-time migration. | Existing GSWA Harvey-1 corpus visible to its assigned owner only | Open |
| **C-47** | Realtime: backend writes `jobs.status` / `jobs.current_phase` / `jobs.pages_processed` transitions to Postgres. Supabase Realtime fans them out. Frontend subscribes to `jobs WHERE owner_id = me`. Polling endpoints stay (backwards-compat) but become a fallback. | Frontend sees status change on `processing → completed` within 200 ms of DB write | Open |
| **C-48** | Reserve URL shape `PATCH /documents/{id}/visibility` (501 in v1.0; ships in v1.1 alongside explicit per-user share primitive). | OpenAPI documents the future shape | Open |

**Realistic timeline (revised for Supabase-native).** Net effort is similar to the v1.1 plan but the deliverable is much stronger — we replace polling, fix `doc_name` at the data-model level, and inherit RLS-enforced storage.

```
Week 1: PR-5 — Supabase project provisioned, JWT verification, /auth/me, RLS on users_profile
Week 2: PR-6 — Storage buckets + file_storage.py rewrite + signed-URL serving + worker cutover
Week 3: PR-7a — documents/jobs tables + Qdrant payload migration + scope filtering
Week 4: PR-7b — backfill CLI + Realtime wiring + integration tests + cutover smoke
```

Frontend continues against MSW mocks during weeks 1–2. Real integration starts week 3 once `/auth/me` and the documents/jobs schema land.

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

### 3.4 `doc_name` is NOT deterministic ⚠️

**This is the single biggest mental-model mismatch in the frontend docs.**

- `doc_name` is computed as `f"{safe_stem}_{job_id[:8]}"` (see `_job_scoped_doc_name` in [app/routes/ingest.py](../app/routes/ingest.py)).
- This is **intentional** — concurrent ingestions of the same PDF must not collide on disk under `data/output/`.
- **Implication for frontend:** any block that says "group by `doc_name`" (Asset Manager portfolio, QA Steward dedupe, Geologist prevalence) will treat every re-ingest of the same file as a separate well.
- **Recommendation:** group by **`filename`** (the original uploaded name, preserved on the manifest). It's the right semantic key.
- If frontend wants a stable, semantic doc identifier (e.g. WGS-spec-compliant well name from L2 entity resolution), that's a future ask — not v1.

### 3.5 Page image URL semantics (B-006)

- Content-type: always `image/png`.
- Cache: C-04 adds `Cache-Control: public, max-age=86400, immutable` (page PNGs never change for a given job_id).
- Out-of-range `n` (≥ `total_pages`) → **404** (matches frontend preference).
- Bbox out of `0..1000` range → **400** with detail (already enforced).

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

- **Default role is always `drilling_engineer`.** Promotions are an admin operation, not a signup field. We will not honor any role hint in `signUp({ data: { role: ... } })` — the trigger ignores it.
- **Display name field name is locked to `display_name`.** If you need a different shape, ping us before changing the trigger.
- **Email confirmations are ON by default** in the Supabase project. Until the user clicks the link, `signInWithPassword` returns `Email not confirmed`. Surface a useful message.
- **Open public sign-up.** Anyone with the anon key can call `signUp`. For the internal-clients launch we strongly recommend one of two postures:
  1. **Invite-only (recommended):** disable public sign-ups in Supabase Dashboard → Authentication → Providers → Email → "Enable email signups" off. Admins create users via the dashboard or the future `POST /admin/users/invite` endpoint.
  2. **Domain allow-list:** keep sign-up on, add a Supabase Auth Hook (`before_user_created`) that rejects non-corporate emails. We can write the hook when product confirms the domain list.
- **Anon key is safe to ship to the browser.** It's rate-limited and scoped by RLS. Service-role key is **never** sent to the browser — backend-only.

### 11.5 Still open — what the backend will add when asked

These are out of PR-5 scope and not started; flag the priority and we'll slot them in.

| Need | Endpoint | Auth | Why deferred |
|---|---|---|---|
| Promote / demote a user | `PATCH /admin/users/{id}/role` | super_admin only | Not blocking signup; can be done via SQL editor today |
| Invite a user from inside the app | `POST /admin/users/invite` (wraps `auth.admin.invite_user_by_email`) | super_admin only | Same; Supabase dashboard works for now |
| Self-service display name change | `PATCH /auth/me/profile` (display_name only) | self | Trivial; ship with PR-7 alongside `users_profile` write paths |

### 11.6 Smoke recipe (run against any environment)

```bash
# from the backend repo, with .env pointed at the target project:
python -m app.tools.e2e_auth
```

This script: admin-creates a throwaway user, asserts the trigger ran, signs in, hits `/auth/me`, then deletes the user. Frontend can run the same flow from a Playwright test once you wire sign-up.

---

*End of Backend → Frontend Communication v2.1 — 2026-05-06 (PR-5 shipped, sign-up flow documented).*
