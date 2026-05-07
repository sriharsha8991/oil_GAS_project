# WellSynth AI — Frontend → Backend Report

> **Purpose:** Living document the frontend team uses to communicate to the backend team. Updated at the **end of every phase** of [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). Records open questions, contract clarifications needed, schema mismatches found, modification requests, and integration milestones.
>
> **How to read:** Each phase has its own section. The **Open Items** table is the canonical to-do for backend. Resolved items move to the **Resolved** subsection of the relevant phase, with the resolution noted.
>
> **Last updated:** 2026-05-07 — **v1.7**. End-to-end FE audit completed (zero direct Supabase data-layer calls — everything flows through backend HTTP API per the platform constraint). New asks **B-018..B-030** filed: 5 missing endpoints (briefings, lessons, entities/distinct, compare, admin/stats), 1 architectural ask (replace ingest polling with backend-owned SSE — FE must NOT subscribe to Supabase Realtime directly), and 7 contract clarifications. See [§0.1 v1.7 New Asks](#01-v17-new-asks-2026-05-07) below.
>
> **Last updated:** 2026-05-06 — v1.6. Backend response **v2.5** received and reconciled. **B-014 (CORS preflight 401) fixed and verified by backend** (`OPTIONS /chat → 204`). FE will keep the `/api/backend` Next.js proxy in place for defense-in-depth; it's now optional rather than load-bearing. Status corrections applied: **B-001 / B-002 / B-005 / B-012 / B-013 are all ✅ Resolved** (FE doc was stale). **B-015 / B-016 / B-017 answered in writing** (see [communication_from_backend.md §0.0.4–§0.0.6](communication_from_backend.md#004-b-015--documentsidfull-content-type-lock-in--confirmed)). **Auth guardrail re-stated:** no `/auth/signup`, no `/auth/login`, no `PATCH /auth/me { role }` — Supabase JS SDK owns auth, backend exposes only `GET /auth/me` + `PATCH /auth/me/profile { display_name?, organization? }`. FE already conforms. Legacy `data/output/<job_id>/manifest.json` filesystem path is **deprecated** — Postgres `jobs` row + Realtime is the source of truth.

---

## 0.1 v1.7 New Asks (2026-05-07)

> **Context:** Full FE → backend retrospection completed 2026-05-07. Platform rule reaffirmed: **frontend MUST NEVER touch Supabase DB / Storage / Realtime directly.** Only `supabase.auth.*` (session + JWT) is permitted in the browser. Everything else — including ingest progress streaming — must go through backend HTTP endpoints. Audit found zero violations of this rule today; this section files the endpoints and clarifications needed to keep it that way as P1b/P2/P3 features land.
>
> **Owner:** Backend team. **FE status:** Wired & ready against typed mocks where possible; blocked on the items below for real-data demos.

### 0.1.1 Snapshot table

| ID | Ask | Phase / Window | Priority | Status |
|---|---|---|---|---|
| **B-018** | `POST /briefings/pre-spud` + `GET /briefings[?owner=]` + `GET/DELETE /briefings/{id}` | P1b (PR-3) | 🔴 Blocker | Open |
| **B-019** | `POST /lessons/applicable` + `GET /lessons[/{id}]` | P1b (PR-4) | 🔴 Blocker | Open |
| **B-020** | `GET /entities/distinct?type=&q=&limit=` | P1b (with PR-3) | 🔴 Blocker | Open — restated from B-008 (C-13) |
| **B-021** | `POST /compare` (409 if either job not `succeeded`) | P2 | 🟡 Important | Open — restated from C-10 |
| **B-022** | `GET /admin/stats` + `GET /admin/stats/sparkline` | P5 | 🟡 Important | Open — restated from C-20/C-21 |
| **B-023** | **Backend-owned SSE for ingest progress** (`GET /ingest/events`) — replace polling. FE must NOT subscribe to Supabase Realtime from the browser. | P5 (or sooner if scale demands) | 🟢 Architectural | Open |
| **B-024** | Confirm `GET /auth/me` always returns `role` (never null); default for new users = `drilling_engineer` | Before P1a demo | 🟢 Clarification | Open |
| **B-025** | Confirm `GET /ingest/{job_id}` shape + error code on not-owner (404 vs 403) | Before P1a merge | 🟢 Clarification | Open |
| **B-026** | Confirm crop bbox coordinate system: normalized `0..1000` (per existing docs) or pixel-space? Aspect handling? | P1b | 🟢 Clarification | Open |
| **B-027** | `GET /admin/users?limit=` — max value, behavior on overflow (422 vs silent cap) | P1a | 🟢 Clarification | Open |
| **B-028** | `POST /ingest/upload` partial-failure semantics — `207 Multi-Status` or all-or-nothing? | P1a | 🟢 Clarification | Open |
| **B-029** | Confirm `GET /health` exists, is unauthenticated, returns `{status, version, database, qdrant}` | P6 | 🟢 Clarification | Open |
| **B-030** | Confirm `?scope=mine\|all` was fully collapsed: super_admin sees all on every list endpoint with no query param; non-admins always implicitly scoped. | Before P1a demo | 🟢 Clarification | Open |

### 0.1.2 B-018 — Briefings endpoints (BLOCKER for P1b)

**Why:** Drilling Engineer dashboard imports `MyBriefingsBlock`. Briefing detail + creation form are wired against typed mocks. Mock will be flipped off the moment the endpoint shape is confirmed.

**Requested shape:**

```
POST /briefings/pre-spud
  Body:  { well_name: string,
           formations?: string[],
           operations?: string[],
           depth_min?: number,
           depth_max?: number,
           project_id?: uuid,
           owner?: string }       // echoed back per C-15
  Resp:  201 { id, owner, well_name, content,
               applicability: { reasons: [...], score },
               sources: SourceRef[],
               empty_state_reason?: BriefingEmptyReason,
               created_at }

GET /briefings?owner=&project_id=&limit=&offset=
  Resp:  200 { items: Briefing[], total, limit, offset }

GET /briefings/{id}      → 200 Briefing | 404 not_found | 403 not_owner
DELETE /briefings/{id}   → 204
```

**Locked enums (already accepted in §3.7 of communication_from_backend.md):**
- `applicability.reasons[]`: `formation_match | operation_match | depth_band_match | section_match`
- `empty_state_reason`: `no_offsets_in_field | no_formation_overlap | corpus_too_small`

### 0.1.3 B-019 — Lessons endpoints (BLOCKER for P1b)

**Why:** Drilling Engineer dashboard imports `LessonsApplicableBlock`. Same mock-flip pattern as briefings.

**Requested shape:**

```
POST /lessons/applicable
  Body:  { document_id: uuid, top_k?: number = 5 }
  Resp:  200 { lessons: [{
           id, title, description,
           applicability: { reasons: LessonReason[], score },
           source: SourceRef,
           created_at
         }],
         empty_state_reason?: LessonsEmptyReason }

GET /lessons[?owner=&applicability=&limit=&offset=]
  Resp:  200 { items: Lesson[], total, limit, offset }

GET /lessons/{id}     → 200 Lesson | 404 not_found | 403 not_owner
```

**Locked enums:**
- `applicability.reasons[]`: same four values as briefings.
- `empty_state_reason`: `no_lesson_keywords_in_corpus | no_classified_lessons | context_too_vague`.

### 0.1.4 B-020 — `GET /entities/distinct` (BLOCKER for P1b)

**Restates B-008 / C-13** with the exact contract FE will bind to:

```
GET /entities/distinct?type=<formation|field|operation|section|well_name>
                     &q=<prefix>&limit=20

Resp: 200 { type: string, values: string[] }
```

**Powers:** Briefing form autocomplete + Geologist "Formation prevalence" chart. P1b demo cannot ship Geologist dashboard or Briefing creation without it.

### 0.1.5 B-021 — `POST /compare` (P2 feature)

**Restates C-10.** FE has feature flag enabled, types ready, mock wired. Need:

```
POST /compare
  Body: { job_ids: [uuid, uuid] }
  Resp: 200 CompareResult     // shape per BACKEND_SPEC §6.2
        409 conflict          // if either job.status != "succeeded"
```

### 0.1.6 B-022 — `GET /admin/stats` + sparkline

**Restates C-20 / C-21.** Today FE aggregates from `GET /ingest` client-side — fine for ≤50 jobs, won't scale. Need server aggregation:

```
GET /admin/stats?days=7
  Resp: { total_jobs, counts_by_status: {queued, running, succeeded, failed},
          total_spend_usd, total_tokens,
          per_model_breakdown: { flash: {jobs, tokens, cost}, pro: {...} },
          users_count, documents_count, projects_count }

GET /admin/stats/sparkline?metric=spend|jobs&days=7
  Resp: { metric, points: [{ ts, value }, ...] }
```

Cache 30s server-side. FE polls at 30s.

### 0.1.7 B-023 — **Backend-owned SSE for ingest progress** (architectural)

**Why this is in the report (and not just "use Supabase Realtime"):**

Backend's earlier plan ([communication_from_backend.md §9.5](communication_from_backend.md#95-realtime--polling-dies)) suggested the FE subscribe directly to the `jobs` table via Supabase Realtime:

```ts
// ❌ Forbidden by platform rule
supabase.channel('jobs:user_id=...').on('postgres_changes', ...).subscribe()
```

**Per the FE/platform constraint, the browser MUST NEVER touch the Supabase DB directly — including Realtime channels.** Auth is the only exception. Therefore Supabase Realtime stops at the backend; the backend re-broadcasts to the FE over SSE.

**Requested endpoint:**

```
GET /ingest/events                       (text/event-stream, JWT-required)

  event: job_update
  data: { job_id, document_id, status, current_phase, pages_done, pages_total,
          tokens?, cost_usd?, error?, updated_at }

  event: heartbeat
  data: { ts }                            // every 15s, keeps proxy alive
```

Backend subscribes to its own Postgres `jobs` channel internally and fans out to authenticated SSE consumers, scoped by `request.state.user.id` (super_admin sees all). FE drops the `refetchInterval` polling in `useJobs()` and consumes this stream instead.

**Acceptable interim:** keep polling. **Not acceptable:** frontend opening a Supabase Realtime channel.

### 0.1.8 B-024..B-030 — Clarifications

Concise, in writing, please add to [communication_from_backend.md](communication_from_backend.md) §0.0.7 or a new §0.0.8:

- **B-024** — Does `GET /auth/me` always return a non-null `role`? What's the default for a freshly-signed-up user (assumed `drilling_engineer` per `handle_new_user` trigger). Lock the role enum in OpenAPI.
- **B-025** — `GET /ingest/{job_id}` — confirm response shape (same `JobRow` as list items?). Confirm error codes: `404` for missing, `403` for not-owner-not-admin?
- **B-026** — `/documents/{id}/pages/{n}/image/crop?ymin&xmin&ymax&xmax` — confirm coordinate space (existing docs imply `0..1000` integer scale). Out-of-range → `400`. Aspect ratio: preserved or stretched?
- **B-027** — `GET /admin/users?limit=` — what's the max accepted `limit` value? Above-max behavior: `422` or silently cap?
- **B-028** — `POST /ingest/upload` with N files: if 1 of N fails (e.g., unsupported MIME), do we get `207 Multi-Status`, or is the whole request rejected? FE's `UploadResponse.jobs[*].status` shape suggests partial-success is possible; please confirm.
- **B-029** — `GET /health` — confirm it exists, is unauthenticated (no JWT required), and the response shape. FE wants to use it for an error-boundary "backend reachable?" probe.
- **B-030** — Per §0 of v2.5, the `?scope=mine|all` toggle was "collapsed into implicit per-caller scoping." Confirm there is **no** `?scope=` query param on any list endpoint (`/projects`, `/documents`, `/ingest`, `/briefings`, `/lessons`, `/admin/users`); super_admin auto-elevation handles "see all"; non-admins are always implicitly owner-scoped.

### 0.1.9 What FE will NOT ask for (non-asks, recorded for symmetry)

- ❌ Direct Supabase Realtime subscriptions from the browser. (B-023 covers this.)
- ❌ Direct Supabase Storage signed-URL generation from the browser. (Backend already issues signed URLs via the 302 on `/documents/{id}/pages/{n}/image`.)
- ❌ Direct Supabase Postgres queries via `supabase.from(...)`. (Audit confirmed FE has zero of these.)
- ❌ Any backend `/auth/signup`, `/auth/login`, or `PATCH /auth/me { role }`. Supabase Auth SDK owns the credential surface; backend owns only `GET /auth/me` + `PATCH /auth/me/profile`.

### 0.1.10 Audit summary (FE compliance check)

| Check | Result | Evidence |
|---|---|---|
| No direct Supabase data-layer calls (`from`, `storage`, `channel`, `rpc`) | ✅ Pass | grep across `lib/`, `app/`, `components/` returns 0 hits |
| `Authorization: Bearer <jwt>` attached to every API call | ✅ Pass | [lib/api.ts](../lib/api.ts) `apiRequest` reads from `supabase.auth.getSession()` |
| Same-origin proxy via Next.js rewrite | ✅ Pass | [next.config.ts](../next.config.ts) maps `/api/backend/*` → `BACKEND_ORIGIN` |
| Middleware does not intercept `/api/*` | ✅ Pass (fixed 2026-05-07) | [middleware.ts](../middleware.ts) matcher excludes `api/` |
| Auth uses Supabase JS SDK only for session/JWT | ✅ Pass | [lib/queries/auth.ts](../lib/queries/auth.ts), [components/layout/auth-bootstrapper.tsx](../components/layout/auth-bootstrapper.tsx) |
| All list endpoints invalidated optimistically on mutate | ✅ Pass | TanStack Query `setQueryData` + `invalidateQueries` pattern in `useCreateProject`, `useUpload`, etc. |

---



Backend team has responded with a full commitment list (C-01 → C-28) in [communication_from_backend.md](communication_from_backend.md). **All 11 frontend asks (B-001 → B-011) accepted.** Frontend signs off on the §2 commitment list as written.

**Three things changed in the frontend's mental model as a result:**

1. **⚠️ `doc_name` is NOT deterministic across re-ingestion.** It's `f"{safe_stem}_{job_id[:8]}"` by design (collision-safe on disk). **All cross-ingest aggregations must group by `filename`, not `doc_name`.** Affects: Asset Manager portfolio block, QA Steward dedupe, Geologist prevalence, Library grouping. Action: sweep [ROLES_AND_DASHBOARDS.md](ROLES_AND_DASHBOARDS.md) and any P1a code to substitute `filename` before P1a merges.
2. **`/chat` will gain `job_ids: string[] | null` in PR-3** (C-14). The FRONTEND_SPEC §28 "system-prefix" workaround for Pinboard cross-doc chat is **no longer needed** — plan on the clean superset.
3. **`POST /demo/seed-sample` (C-19)** with `job_id = sha256(filename)[:16]` replaces the originally-requested env-fixed sample id. Same outcome (deterministic id for onboarding step 2), slightly different shape.

**Frontend asks for backend in return** (per [§6](communication_from_backend.md#6-what-backend-wants-from-frontend)):

- ✅ We confirm the §2 commitment list and target windows.
- ✅ We confirm `filename`-based grouping for cross-ingest aggregation.
- ✅ We accept the locked enums (`applicability.reasons`, both `empty_state_reason` sets) per [§3.7 of the response](communication_from_backend.md#37-locked-enums).
- 🟡 We will reference the static UI ([app/static/chat.html](../app/static/chat.html), [ingest.html](../app/static/ingest.html)) for ambiguous behavior.
- 🟡 No requests to bring C-19 / C-20 / C-21 forward of P5 — P5 timing works.

**Persona suggestion accepted for roadmap:** Wellsite Supervisor / Operations Engineer kept on the post-demo roadmap (planning vs. live-ops counterpart to Drilling Engineer). Will be added to [ROLES_AND_DASHBOARDS.md](ROLES_AND_DASHBOARDS.md) §7 as a v1.1 candidate.

---

## Index

| Phase | Status | Open items |
|---|---|---|
| **P0 — Foundations** | Complete | 6 |
| **P1a — Shell + Ingest + Library + Role Dashboards** | Starting | 7 |
| **P1b — Document Workspace + Chat + Auto-Summary** | **In progress (FE wired against v2.4 OpenAPI)** | **4 new (B-014..B-017)** |
| P2 — Browsers + Search + Compare | Search wired in P1b pass; full Compare UI not started | — |
| P3 — Briefing + Lessons + Pinboard | Not started | — |
| P4 — Reports + Export + Email + Sharing | Not started | — |
| P5 — Premium Polish | Not started | — |
| P6 — Hardening & Launch | Not started | — |

## Top Priorities — status snapshot

All 5 top-priority items have been accepted by backend. Status reflects backend commitment IDs from [communication_from_backend.md §2](communication_from_backend.md#2-backend-commitment-list-canonical):

| # | Ask | Backend commitment | Window | Status |
|---|---|---|---|---|
| 1 | B-001 — OpenAPI export | C-01 | Done | ✅ Resolved |
| 2 | B-002 — CORS allow-list | C-03 | Done | ✅ Resolved |
| 3 | B-008 — `/entities/distinct` | C-13 | With PR-3 | ⏳ Scheduled |
| 4 | B-005 — real `JobManifest` sample | C-02 | Done — superseded by Postgres `jobs` row in PR-7 | ✅ Resolved |
| 5 | B-007 — `/admin/stats` | C-20 | Pre-demo (P5) | ⏳ Scheduled — frontend OK with P5 timing |
| 6 | **B-012 — Accounts + roles in DB** | C-29..C-35, C-49..C-54 | Done | ✅ Resolved (Supabase Auth + `users_profile.role`; FE conforms) |
| 7 | **B-013 — Per-user document ownership** | C-41..C-48 (PR-7) | Done | ✅ Resolved (`(user_id, project_id, document_id)` identity; super_admin auto-elevation) |
| 8 | **B-014 — CORS preflight returns 401 on `/chat`** | v2.5 hot-fix | Done | ✅ Resolved (verified `OPTIONS /chat → 204`) |
| 9 | **B-015 — `/documents/{id}/full` content-type lock-in** | v2.5 §0.0.4 | Done | ✅ Resolved (`text/plain`, markdown body, 404 on no-pages, `Cache-Control: private, max-age=300`) |
| 10 | **B-016 — `/documents/{id}/pages/{n}/image` JWT-on-redirect** | v2.5 §0.0.5 + C-04 | Done | ✅ Resolved (302 → credential-less signed URL, 60s TTL, `Cache-Control: public, max-age=86400, immutable`) |
| 11 | **B-017 — `/search` filter contract + result-shape lock-in** | v2.5 §0.0.6 | Done | ✅ Resolved (envelope `{query,count,results}`; per-hit fields locked; filter semantics confirmed) |

---

## Phase 0 — Foundations

**Frontend status:** Scaffolding the Next.js 15 app, design tokens, theming, AppShell, demo auth, MSW mocks, CI. No real backend calls yet beyond `/api/auth/me` (frontend route).

**Backend impact this phase:** Minimal. We are mocking everything against fixtures derived from BACKEND_SPEC schemas.

### P0 — Open Items for Backend

| ID | Item | Why we need it | Suggested resolution | Priority |
|---|---|---|---|---|
| **B-001** | **Provide an OpenAPI 3.1 schema export** at `GET /openapi.json` (FastAPI gives this for free). Confirm CORS allows the frontend dev origin to fetch it. | We want to auto-generate `lib/types.ts` instead of hand-mirroring. Avoids schema drift across all 4 new endpoints. | Confirm endpoint exists; share dev URL; ensure `Access-Control-Allow-Origin` includes `http://localhost:3000`. | High — needed before P1a integration |
| **B-002** | **Confirm CORS allow-list** for `http://localhost:3000` and the eventual Vercel preview domain pattern. | Frontend dev cannot make any real backend call without this. | Add to backend CORS middleware config. | High |
| **B-003** | **Confirm `Authorization: Bearer <jwt>` is accepted-but-ignored** on every endpoint (per FRONTEND_SPEC §8). | Frontend will attach this header from day one for forward-compat. We need to know it won't cause 401s today. | Pass-through middleware behavior; document in OpenAPI. | High |
| **B-004** | **Sample / fixture corpus for demo deploys** — confirm `GSWA_Harvey_1_WCR.pdf` is present in the demo backend's `data/output` and exposes a stable `job_id`, OR provide a "load sample" endpoint. | FRONTEND_SPEC §34 onboarding step 2 needs a guaranteed sample doc. | Either (a) document the fixed `job_id` in env, or (b) add `POST /demo/seed-sample` admin route. Frontend prefers (a). | Medium — needed by P5 onboarding, but useful in P1a |
| **B-005** | **Job manifest field stability** — confirm the exact JSON shape from `GET /ingest/{job_id}` matches BACKEND_SPEC §1 *and* FRONTEND_SPEC §7.2 (they should already agree). Any extra/missing fields? | Single source of truth for our `JobManifest` type. | Share one real manifest JSON sample (sanitized). Confirm `tokens` and `cost_usd` are always present (not nullable) once `processing` starts. | High |
| **B-006** | **Page image URL stability** — `GET /ingest/{job_id}/pages/{n}/image` and `/crop` — confirm: cache headers, content type (`image/png`), max page number behavior (404 vs 416). | We render `<img src>` directly; need to know how to handle out-of-range requests in the page navigator. | Document expected response codes for n=0, n>total, and bbox out of 0..1000 range. | Medium |

### P0 — Modification Requests / Heads-Ups

None this phase. We are not asking backend to change anything yet — only to confirm the contract surface.

### P0 — Resolved

All P0 asks accepted by backend response v1.0 (2026-05-06):

| ID | Resolution | Backend commitment |
|---|---|---|
| **B-001** | OpenAPI live at `GET /openapi.json`; versioned `openapi.json` will be committed to repo root. | C-01 (this week) |
| **B-002** | Current `allow_origins=["*"]` + credentials is broken; tightening to env-driven allow-list defaulting to `http://localhost:3000`; Vercel previews matched via regex. | C-03 (this week) |
| **B-003** | Confirmed: zero auth middleware in v1, `Authorization: Bearer` is silently accepted. To be documented per route in OpenAPI description. | C-05 (this week) |
| **B-004** | Original ask was env-fixed sample `job_id`. **Backend chose `POST /demo/seed-sample` with deterministic `job_id = sha256(filename)[:16]` instead.** Same outcome; lands pre-demo. | C-19 (P5) |
| **B-005** | Shape matches BACKEND_SPEC §1 + FRONTEND_SPEC §7.2. `tokens` and `cost_usd` non-null on `completed`; partial (never null) once `pages_processed > 0`; `0` on `pending`. Sanitized real fixture committed to `docs/fixtures/job_manifest.json`. | C-02 (this week) |
| **B-006** | `image/png`; `Cache-Control: public, max-age=86400, immutable`; out-of-range `n` → 404; out-of-range bbox → 400. | C-04 (this week) |

---

## Phase 1a — Shell + Ingest + Library + Role-Aware Dashboards

**Frontend status:** P1a is starting. Scope expanded beyond the original spec to include **role-resolved dashboards** — see [ROLES_AND_DASHBOARDS.md](ROLES_AND_DASHBOARDS.md). The five v1 roles are: `super_admin`, `drilling_engineer` (default), `geologist`, `asset_manager`, `qa_steward`.

> **⚠️ Architecture change (2026-05-06):** Roles are **no longer a frontend-only preference**. Per product decision, the user's role is now an **account attribute** captured at signup and stored in the backend. The frontend will read the role from `/auth/me` (or equivalent) and render the matching dashboard — the localStorage `ws.role` slice becomes a UI override only (e.g., "View as…" for Super Admins). This adds **B-012** to the P1a critical path. Until B-012 lands, the frontend keeps the localStorage default and a manual switcher (already shipped in the topbar user menu).

**Backend impact this phase:** Six asks (B-007 through B-012). Critical path is now **B-008** *and* **B-012**.

### P1a — Open Items for Backend

All 5 accepted by backend response v1.0; status reflects scheduled landing window.

| ID | Item | Backend commitment | Window | Status |
|---|---|---|---|---|
| **B-007** | `GET /admin/stats` (counts/spend/tokens) + companion `GET /admin/stats/sparkline?metric=&days=` | C-20 + C-21 | Pre-demo (P5) | ⏳ Scheduled. Frontend does client-side aggregation in P1a (acceptable for ≤100 jobs). |
| **B-008** | `GET /entities/distinct?type={formation\|field\|operation\|section\|well_name}` (note: `well_name` added by backend) | C-13 | With PR-3 | ⏳ Scheduled. Lands alongside Briefing PR; unblocks Geologist prevalence + Briefing autocomplete in one shot. |
| **B-009** | `pages_meta[]` + per-key-fact `confidence` | C-07 + C-08 (+ C-09 backfill) | With PR-1 | ⏳ Scheduled. QA dashboard P5 timing works cleanly. |
| **B-010** | Briefing `owner` echo + `GET /briefings?owner=` filter | C-15 | With PR-3 | ⏳ Scheduled. Frontend will drop the `localStorage` fallback. |
| **B-011** | `GET /admin/events` audit stream | C-23 | Post-demo | ⏸️ Deferred. Not for v1; roadmap visibility only. |
| **B-012** | **Accounts + roles in DB** — see detailed spec below. Critical-path. | *new* | This week / next | 🔴 **NEW — awaiting backend response** |
| **B-013** | **Per-user document ownership / personalised corpus** — see detailed spec below. Critical-path. | *new* | This week / next | 🔴 **NEW — awaiting backend response** |

---

### B-012 — Accounts, Signup, and Role Assignment (full spec)

**Why we need it.** v1 currently treats `role` as a localStorage value. Product wants the role to be a **first-class account attribute**: chosen at signup, stored in the database, returned by the auth API, and used by the backend in the future for RBAC, audit logs, and per-role analytics. The frontend already renders five role-specific dashboards; we just need a trustworthy `role` from the backend on every authenticated request.

**Scope (v1, minimum viable).**

1. **User table.** Persistent users with at least these columns. We are not opinionated about the exact column types; this is a sketch:

   ```text
   users (
     id            uuid primary key,
     email         text unique not null,
     name          text not null,
     password_hash text not null,                -- if password auth; otherwise SSO subject
     role          text not null,                -- one of: super_admin | drilling_engineer | geologist | asset_manager | qa_steward
     created_at    timestamptz not null default now(),
     updated_at    timestamptz not null default now(),
     last_login_at timestamptz null
   )
   ```

   **Locked role enum (must match frontend exactly):**
   `super_admin | drilling_engineer | geologist | asset_manager | qa_steward`.

2. **`POST /auth/signup`.** Public endpoint. Body:

   ```json
   {
     "email": "jane@example.com",
     "name": "Jane Doe",
     "password": "…",
     "role": "drilling_engineer"
   }
   ```

   Behavior:
   - 201 on success, returns the same shape as `/auth/me` (see below) plus a session cookie / JWT.
   - 409 if email is already registered.
   - 422 if `role` is not in the locked enum.
   - **Role is required.** No "default role" on the backend side — the frontend will force the user to pick one in the signup form.

3. **`POST /auth/login`.** Standard email + password (or SSO callback). Returns the same shape as `/auth/me`.

4. **`GET /auth/me`.** Authenticated. Returns the canonical user record:

   ```json
   {
     "id": "…uuid…",
     "email": "jane@example.com",
     "name": "Jane Doe",
     "role": "drilling_engineer",
     "created_at": "2026-05-06T12:34:56Z",
     "last_login_at": "2026-05-06T13:00:00Z"
   }
   ```

   Frontend will call this on every page load to hydrate `useAuthStore` and pick the dashboard layout. Must be cheap (target p95 < 100 ms).

5. **`PATCH /auth/me`.** Authenticated. Allows the user to change their `name` and/or `role`. Body is a partial of the `/auth/me` shape; same validation rules as signup. **Frontend will use this from the topbar role switcher** so the change persists across devices.

6. **Authorization model for v1.** No RBAC enforcement yet — every endpoint stays open to any authenticated user. Backend just needs to (a) persist `role`, (b) return it on `/auth/me`, and (c) accept it on signup/patch. RBAC enforcement is a v1.1 conversation.

7. **Demo seed.** The existing demo account (whatever `jane@demo` shape we have today) should be assigned `role = "super_admin"` so the demo still shows the admin dashboard out of the box. We can add a second seeded account `engineer@demo` with `role = "drilling_engineer"` for the engineer demo path.

8. **CORS / cookies.** No new requirements beyond what B-002 already covers. If session is cookie-based, `Set-Cookie` must include `SameSite=None; Secure` for the Vercel preview domains.

**Frontend changes that follow from this** (no backend action required, listed for context):

- New `/signup` route with email + name + password + **role picker** (5 chips, copy from ROLES_AND_DASHBOARDS §3.2).
- `useAuthStore` will track `{ user: { id, email, name, role } | null }` and read from `/auth/me`.
- `useRoleStore` becomes a UI-only override — it defaults to `user.role` and only diverges when a Super Admin uses "View as…".
- Removed: the first-run role chip on the dashboard becomes redundant once accounts are real; it stays only as a fallback for demo / unauthenticated previews.
- Topbar role switcher will call `PATCH /auth/me` instead of mutating localStorage.

**What we'd like back from backend.**

- Confirm the endpoint shapes above.
- Confirm the role enum string values exactly (we will hard-fail if they drift).
- Target landing window. Frontend can ship the role-aware dashboards behind `useRoleStore` today; we'd like B-012 in **P1a or P1b** so we can drop the localStorage default before the demo.
- Whether you'd like the frontend to send `role` as part of an `Authorization` JWT claim (preferred) or always re-fetch from `/auth/me`. Either works.

---

### B-013 — Per-user document ownership / personalised corpus (full spec)

**Why we need it.** Original BACKEND_SPEC treats the corpus as a single shared pool — every authenticated user sees every job, every briefing, every lesson. Product has decided the experience should be **personal first, shared on opt-in**: each user uploads, ingests, and curates their own documents; cross-user visibility happens only when an owner explicitly shares (or, for a Super Admin, via an "all users" admin view).

This is a foundational change because *every list endpoint we already use* needs to default to "current user only."

**Locked terminology (must match frontend exactly):**

- `owner_id` — UUID of the user who owns the resource. Set at create time, immutable.
- `visibility` — `private` (default) | `shared` | `org`. v1 implements `private` and `org`; `shared` is reserved for v1.1 explicit per-user shares.
- "Org" — for v1 there is exactly one org. Don't model orgs as a table yet; just treat `org` visibility as "every authenticated user can read."

**Scope (v1, minimum viable).**

1. **Add `owner_id` to every user-generated resource.** New columns / fields:

   | Resource | Where it lives today | Add |
   |---|---|---|
   | Ingest job (`JobManifest`) | `data/output/<job_id>/manifest.json` + DB row | `owner_id: uuid not null`, `visibility: text not null default 'private'` |
   | Briefing | `briefings` table | `owner_id`, `visibility` (already echoed via C-15; formalise here) |
   | Lesson | `lessons` table | `owner_id`, `visibility` |
   | Chat session | `chat_sessions` table (if persisted) | `owner_id`, `visibility` (sessions are always `private` in v1) |
   | Pinboard (P3) | new `pinboards` table | `owner_id`, `visibility` |

   **Backfill.** Existing demo data should be assigned to the seeded `super_admin` demo account with `visibility = 'org'` so it stays visible in the demo.

2. **Filter every list endpoint by ownership.** All of these change from "return everything" to "return resources owned by the caller OR `visibility in ('org')`":

   - `GET /ingest`
   - `GET /briefings` (already filterable by `owner` per C-15 — make this the default rather than opt-in)
   - `GET /lessons` (when added)
   - `GET /search` — chunks are filtered by the owning job's visibility
   - `GET /entities/distinct` (B-008) — same, restrict to entities from visible jobs
   - `POST /chat` — `job_id` and `job_ids` must reference jobs the caller can see; otherwise 403

3. **Admin override for Super Admin.** Add a `?scope=` query param on every list endpoint with two values:

   - `scope=mine` (default for everyone) — only `owner_id == caller`
   - `scope=all` — only honored if `caller.role == 'super_admin'`; returns every resource regardless of `owner_id`. For other roles the backend may either 403 or silently fall back to `mine` — frontend prefers **403** so the UI can hide the toggle correctly.

   This lets the Super Admin dashboard render a true platform-wide ingestion-health board while every other role gets a personalised library.

4. **Authorship and stamping.**

   - `POST /ingest/upload` — backend reads the caller from session and writes `owner_id = caller.id`, `visibility = 'private'`. No change to the request body.
   - Same pattern for `POST /briefings/pre-spud`, `POST /lessons`, `POST /chat`.
   - `JobManifest` response now includes `owner_id` and `visibility` so the frontend can render an "owned by you / shared with org" chip.

5. **Sharing primitive (deferred to v1.1, but please reserve the shape).** Eventually a user can flip a job from `private` to `org`. Suggested endpoint:

   ```text
   PATCH /ingest/{job_id}/visibility
   { "visibility": "org" }
   ```

   Authorisation: `owner_id == caller` OR `caller.role == 'super_admin'`. We don't need this in P1a; we just want to lock the URL/shape so the frontend can plan the UI.

6. **Errors.**

   - 403 `not_owner` when a user tries to GET / DELETE a resource that isn't theirs and isn't `org`.
   - 403 `requires_super_admin` when a non-admin passes `scope=all`.
   - 404 `not_found` when the resource doesn't exist (don't leak existence to non-owners).

7. **Demo seeds.** Two seeded users (per B-012):

   - `admin@demo` — `super_admin`. Sees everything via `scope=all`.
   - `engineer@demo` — `drilling_engineer`. Owns the GSWA Harvey-1 sample so its dashboard is non-empty out of the box.

**What it means for the dashboards we are building.**

- **Drilling Engineer / Geologist / Asset Manager / QA Steward** dashboards default to **the caller's own corpus**. "Active well" / "Recently viewed" / "My briefings" all become personal automatically — no extra filter logic on the frontend.
- **Super Admin** dashboard explicitly opts in via `?scope=all` so the ingestion-health and spend boards aggregate across every user.
- A small "Owned by you · 12 docs" / "Shared in org · 47 docs" pill on the Library page lets users see the split.

**Frontend changes that follow** (no backend action required, listed for context):

- `useJobs()` will pass `?scope=` based on role: `all` for `super_admin`, `mine` (default) for everyone else.
- New `useRoleStore` selector `effectiveScope()` returns `'all' | 'mine'`.
- Library page gains a scope filter (only visible to Super Admins).
- Mocks (`tests/mocks/handlers.ts`) get an `owner_id` field on the seeded job and respect `scope`.

**What we'd like back from backend.**

- Confirm the `owner_id` / `visibility` column names and enum values exactly.
- Confirm the `?scope=mine|all` parameter on every list endpoint that touches user-generated data.
- Confirm error codes (`not_owner`, `requires_super_admin`).
- Target landing window. We'd love B-013 to land **with B-012** since they're conceptually one PR (auth + ownership). If they have to split, B-012 first.
- Whether visibility should be propagated to the vector store filter (Qdrant payload index on `owner_id`) so cross-user search isolation is enforced at the vector layer too — frontend recommends yes, but defers to backend on cost.

---

### P1a — Heads-Up: visual refinement (no backend impact)

The frontend is applying a small visual refinement (token tweaks, Inter tabular numerals, denser default, no card-top gradients — see [ROLES_AND_DASHBOARDS.md §5](ROLES_AND_DASHBOARDS.md)). **No contract changes.** Mentioned here so the backend team isn't surprised by screenshot diffs in the next demo.

### P1a — Bonus item volunteered by backend

- **C-14** — `/chat` request gains `job_ids: string[] | null` (strict superset). Removes the FRONTEND_SPEC §28 v1 single-doc limitation for Pinboard "Chat with this board." Frontend updates: drop the system-prefix workaround in P3.

### P1a — Anticipated clarifications (all answered by backend response v1.0)

| Question | Answer | Source |
|---|---|---|
| Pagination on `GET /ingest`? | Today returns up to 100, no pagination. C-12 adds keyset `?limit=&before=<job_id>`. Default unchanged. | [§3.8](communication_from_backend.md#38-ingest-pagination) |
| `current_phase` enum stability? | Locked to `extraction \| validation \| escalation \| postprocessing \| indexing`. Only set while `status == processing`. | [§3.6](communication_from_backend.md#36-manifest-fields--enums) |
| ETA hint? | Backend will not compute. Client-side `pages_processed / elapsed` is the agreed approach. | [§3.9](communication_from_backend.md#39-eta--current_phase--cost_usd-confirmations) |
| `doc_name` stability? | **⚠️ Not stable** — `f"{safe_stem}_{job_id[:8]}"` by design. **Group by `filename`** for cross-ingest aggregation. See banner at top of this doc. | [§3.4](communication_from_backend.md#34-doc_name-is-not-deterministic) |
| `cost_usd` reliability? | Non-null on every `completed` job; partial on `processing` (never null after `pages_processed > 0`); `0.0` on `pending`. | [§3.3](communication_from_backend.md#33-jobmanifest-stability-b-005) + [§3.9](communication_from_backend.md#39-eta--current_phase--cost_usd-confirmations) |

### P1a — Resolved

*(items move here as backend PRs merge — currently all 5 are scheduled, none merged)*

---

## Phase 1b — Document Workspace + Chat + Auto-Summary

**Anticipated frontend asks (preview):**

- **`GET /documents/{job_id}/summary` rollout window** — when does PR-1 land? We need to flip mocks to real.
- **Behavior when summary is missing** (job ingested before backfill ran) — does the endpoint return 404 or a 200 with `summary: null`? Frontend prefers 404 so we can render the empty state cleanly.
- **Backfill CLI** — confirm `python -m app.tools.backfill_summaries` is run once on the demo deploy after PR-1 merges.
- **`/chat` `attached_pages[]` URL** — are these absolute or relative? FRONTEND_SPEC §7.1 shows relative; we'll prefix with `NEXT_PUBLIC_API_URL`.
- **Streaming** — confirm `/chat` is request/response only (no SSE) for v1. Frontend implements typewriter on the client.
- **`session_id` lifetime** — is it persisted server-side or purely a client-issued UUID? Affects whether `GET /chat/sessions/{id}` is a future ask.

### P1b — Open Items for Backend

*(populated at phase start)*

---

## Phase 2 — Browsers + Search + Compare

**Anticipated frontend asks (preview):**

- **`POST /compare`** — confirm PR-2 lands before P2 freeze. Confirm response shape matches BACKEND_SPEC §6.2.
- **Search filters** — `GET /search` accepts `score_threshold`, `job_id`, `doc_name`, `page_num`. Confirm multi-value filters (e.g., `kind in [text, table]`) — current spec doesn't show them; frontend will fan out N requests if needed but a single call is preferable.
- **Compare entry points** — when both jobs not yet `completed`, backend returns 409. Frontend will gray-out compare CTA; confirm 409 is the right code.
- **Tables CSV/JSON** — frontend converts markdown → CSV. Is there a `GET /ingest/{job_id}/tables/{table_id}.json` raw endpoint we can use instead? FRONTEND_SPEC §7.2 mentions it; confirm.

---

## Phase 3 — Briefing + Lessons + Pinboard

**Anticipated frontend asks (preview):**

- **`POST /briefings/pre-spud`** — latency budget? Demo flow asks user to wait; under 30 s feels fine, over 60 s needs a "still working…" UX. Confirm p50/p95.
- **Briefing form autocomplete** — formations, fields. **Now formalized as B-008** in P1a (so it lands earlier alongside the Geologist dashboard). Decision: backend exposes `GET /entities/distinct?type={formation|field|operation|section}`. Reference [P1a Open Items](#p1a--open-items-for-backend).
- **`POST /lessons/applicable`** — confirm `top_k` cap and default. Confirm `applicability.reasons` enum values (`formation_match | operation_match | depth_band_match | section_match` — anything else?).
- **Pinboard "chat with this board" cross-doc support** — current `/chat` accepts a single `job_id` / `doc_name`. Frontend v1 sends most-pinned doc + system-prefix listing pinned pages (per FRONTEND_SPEC §28). **Ask:** is backend willing to add `job_ids: string[]` to `/chat` request as a strict superset before P3, or do we live with the limitation? Affects demo polish.
- **Empty-state reasons** — confirm `empty_state_reason` string enum values for both briefings and lessons so we can localize.
- **Briefing PDF export** — done client-side via `@react-pdf/renderer`. Backend doesn't need to do anything, just confirming.

---

## Phase 4 — Reports + Export + Email + Sharing

**Anticipated frontend asks (preview):**

- **Long-lived asset URLs** — share tokens link to chat sessions and reports rendered server-side from frontend state. We don't need any backend share concept. Confirming this stays out of backend scope.
- **Webhook for ingestion completion → email digest** — out of scope for v1; flagging as a future ask so backend can plan.

---

## Phase 5 — Premium Polish

**Anticipated frontend asks (preview):**

- **Activity feed source** — frontend is the source of truth for activity events. Backend exposes nothing here. Confirming no scope creep.
- **Map coordinates** — entities of type `latitude` / `longitude` are surfaced in the entity store. Confirm units are decimal degrees, WGS84.

---

## Phase 6 — Hardening & Launch

**Anticipated frontend asks (preview):**

- **Production CORS** — final allow-list of frontend origins.
- **Rate limits** — any per-IP or per-endpoint rate limits we need to handle (especially during a live demo with concurrent users)?
- **Failure modes** — list of expected 5xx scenarios so error boundaries surface the right copy.
- **Health endpoint** — `GET /health` for uptime monitoring; confirm exists and is unauthenticated.

---

## Glossary of Recurring Asks

These themes show up across phases — backend can answer them once and frontend will reference the answer.

1. **OpenAPI export** (B-001) — single source of truth for types.
2. **CORS allow-list** (B-002) — needed at every environment promotion.
3. **Auth header pass-through** (B-003) — forward-compatibility.
4. **Empty-state reason enums** — referenced in P3 briefings/lessons.
5. **Pagination contracts** — referenced in P1a (`/ingest`) and P2 (`/search`).
6. **Asset URL conventions** — absolute vs relative; cache headers; failure codes.
7. **Distinct-value endpoints** (B-008) — autocomplete for forms + dashboard aggregations.
8. **Aggregation endpoints** (B-007) — admin-level rollups for Super Admin dashboard.

---

## Addendum: Role-Aware Dashboards (added 2026-05-06)

The frontend has decided to ship **role-resolved dashboards** in P1a instead of a single generic dashboard. Five v1 roles: `super_admin`, `drilling_engineer` (default), `geologist`, `asset_manager`, `qa_steward`.

**What this changes for backend:**

- **Nothing required for v1.** Roles are a frontend `localStorage` preference. No RBAC, no per-user tenancy, no new auth.
- Five new asks added (**B-007 → B-011**, see [P1a Open Items](#p1a--open-items-for-backend)). Only **B-008** is on the critical path; all others have client-side fallbacks.
- Visual refinement (denser layout, mono numerals for measurements, no card gradients) is happening in parallel — no contract changes.

**Companion doc:** [ROLES_AND_DASHBOARDS.md](ROLES_AND_DASHBOARDS.md) — full per-role analysis, block library architecture, visual direction, and rationale.

**TL;DR for the backend team:**

> If you can land **B-008** alongside the Briefing PR (BACKEND_SPEC §9 PR-3), and **B-007** any time before the demo, that covers the entire role-aware dashboard roadmap for v1. B-009/B-010/B-011 can wait.

---

## Addendum: v2.4 OpenAPI Integration Pass (added 2026-05-06)

The frontend completed an end-to-end wire-up against the committed [backendopenapi.json](backendopenapi.json) (FastAPI 0.6.0). Every surface listed in the spec — auth/me, projects CRUD, documents, ingest, admin/users, **chat**, and **search** — now has typed query hooks, MSW handlers, and at least one production page consuming it. This pass surfaced **one blocker (B-014)** and **three contract clarifications (B-015 / B-016 / B-017)**.

### What the FE shipped this pass

| Surface | Page / hook | Notes |
|---|---|---|
| `POST /chat` | `useChat()` + `app/(main)/chat/page.tsx` | Session continuity via server-issued `session_id`. Project + document scope dropdowns. Renders `answer`, `claims`, `unanswered`, `sources`, `evidence` (incl. `table_markdown`, `bbox` crops, image URLs). |
| `GET /search` | `useSearch()` + `app/(main)/search/page.tsx` | Filters: `q`, `limit`, `project_id`, `document_id`, `page_num`, `score_threshold`. Deep-linkable from the document detail page. |
| `GET /documents/{id}/full` | `app/(main)/documents/[documentId]/page.tsx` | Fetched as **text/plain markdown** (was previously a wrong `<iframe>` PDF assumption). Rendered in a `<pre>` with whitespace-pre-wrap. |
| `GET /documents/{id}/pages/{n}/image` | Same page | JWT-authenticated `fetch` → blob → `URL.createObjectURL`, used as `<img src>`. Page-thumb rail + main preview. |
| Type alignment | `lib/types.ts` | `Project.updated_at` made required; `ChatRequest` switched to `project_id`/`document_id` scope; `ChatSource`/`ChatEvidence` widened to nullable per OpenAPI; `SearchHit` open index-signature. |
| Nav flags | `lib/flags/config.ts` | `nav.chat` and `nav.search` flipped to `defaultEnabled: true` (P1b shipping window). |
| Same-origin proxy | `next.config.ts` | New rewrite `/api/backend/:path*` → `${BACKEND_ORIGIN}/:path*`. Default `API_URL` flipped to `/api/backend`. **Defensive workaround for B-014.** |

### v2.4 Pass — Open Items for Backend

#### B-014 — CORS preflight returns 401 on `/chat` (and likely every route)

**Severity:** 🔴 Blocker for any browser client that talks to the backend cross-origin (i.e. the entire FE in dev, and prod if the FE and API are on different domains).

**Symptom (verbatim from devtools):**

```
Request URL:    http://localhost:8080/chat
Request Method: OPTIONS
Status Code:    401 Unauthorized
Referrer Policy: strict-origin-when-cross-origin
```

**Root cause (suspected).** The CORS preflight (`OPTIONS`) is reaching an authentication dependency before `CORSMiddleware` can short-circuit it. CORS preflights **must be unauthenticated** by spec — the browser sends them without credentials and a 401 means the browser will never issue the actual `POST`. Most likely one of:

1. A global `Depends(get_current_user)` is mounted on the app or router (not per-route), so even `OPTIONS` is gated.
2. `CORSMiddleware` is added *after* the auth middleware in the middleware chain.
3. Auth middleware is custom (not `Depends`-based) and doesn't bail on `request.method == "OPTIONS"`.

**What we need from backend.**

1. Add (or re-order) `CORSMiddleware` so it runs **before** any auth middleware:

   ```python
   from fastapi.middleware.cors import CORSMiddleware

   app.add_middleware(
       CORSMiddleware,
       allow_origins=[
           "http://localhost:3000",
           "https://*.vercel.app",          # preview deploys
           "https://app.wellsynth.ai",       # prod
       ],
       allow_credentials=True,
       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
       allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
       expose_headers=["X-Request-Id"],
       max_age=600,
   )
   ```

2. Make sure auth dependencies are **per-route** (or, if app-level, that they early-return on `OPTIONS`):

   ```python
   @app.middleware("http")
   async def auth_middleware(request, call_next):
       if request.method == "OPTIONS":
           return await call_next(request)   # let CORSMiddleware answer it
       # …existing JWT check…
   ```

3. Confirm the same fix is in place for **every route**, not just `/chat`. Likely affected based on the OpenAPI surface: `/auth/me`, `/auth/me/profile`, `/projects`, `/projects/{id}`, `/documents`, `/documents/{id}`, `/documents/{id}/full`, `/documents/{id}/pages/{n}/image`, `/documents/{id}/pages/{n}/image/crop`, `/ingest`, `/ingest/{id}`, `/ingest/upload`, `/admin/users`, `/admin/users/{id}/role`, `/admin/users/invite`, `/chat`, `/search`.

**FE mitigation already shipped.** Browser requests now go through a same-origin Next.js rewrite at `/api/backend/*` → `BACKEND_ORIGIN`. Same-origin requests don't trigger CORS preflight, so the 401 disappears in dev. **This is a workaround, not a fix** — production deployments where the FE and API are on different domains will still hit the bug if backend doesn't fix CORS.

---

#### B-015 — `/documents/{id}/full` content-type lock-in

**Severity:** 🟡 Clarification — FE has shipped to the assumed contract.

The OpenAPI says the endpoint returns `text/plain`. FE now fetches it as text and renders the body as markdown in a `<pre>` block. **Please confirm:**

1. Response is always **`Content-Type: text/plain; charset=utf-8`** (not `application/json`, not `application/pdf`).
2. Body is **markdown** (the combined per-page extraction text), not e.g. JSON-stringified pages.
3. Behavior when the document has no successfully ingested pages — 200 with empty body, 404, or 409? FE prefers **404 with `error_code: "no_pages"`** so we render the right empty state.
4. Cache headers — currently FE caches for 60 s in TanStack Query. If backend recommends a longer / shorter TTL, please add `Cache-Control` to the response.

If backend would prefer FE to render structured markdown via a real markdown component, we can pull in `react-markdown` — flag this and we'll do it. Today it's `<pre className="whitespace-pre-wrap">` which is functional but not styled.

---

#### B-016 — `/documents/{id}/pages/{n}/image` JWT-on-redirect behaviour

**Severity:** 🟡 Clarification — FE has shipped a defensive blob-fetch implementation.

Per OpenAPI this endpoint returns a **302 redirect to a signed Supabase Storage URL**. The browser will follow the redirect transparently when used as `<img src="…">` *only if* the JWT can ride along, which generally requires either (a) the signed URL to embed credentials, or (b) the image fetch to be same-origin (no `crossorigin` mode). Naïve `<img src={pageImageUrl(id, n)}>` fails because the image request goes out without `Authorization`.

**FE's current implementation:**

```ts
const res = await fetch(pageImageUrl(documentId, pageNum), {
  headers: { Authorization: `Bearer ${token}` },
});
const blob = await res.blob();
const objectUrl = URL.createObjectURL(blob);
// then <img src={objectUrl} />
```

This works because we attach the JWT on the first hop, the backend 302s to a short-TTL signed URL, and `fetch` follows the redirect server-to-server. **Please confirm:**

1. The redirect target is a **public signed URL** (no `Authorization` needed on the second hop). We assume yes — Supabase Storage signed URLs are credential-less by design.
2. The signed URL TTL — ours assumes ≥ 60 s so the browser can finish the redirect chain. Please confirm.
3. CORS on the signed URL — not strictly needed because we're reading it as a blob, but if backend has any control over the Storage bucket CORS, please ensure it allows the FE origin.
4. Same questions apply to `/documents/{id}/pages/{n}/image/crop`.

If backend can instead **stream the PNG directly** (i.e. return `200 + Content-Type: image/png` instead of 302), FE could go back to `<img src={url}>` with credentials and skip the blob dance. That's a nicer UX (browser caching, lazy-load) but more bandwidth on the API server. **No strong preference — flag whichever is easier.**

---

#### B-017 — `/search` filter contract + result-shape lock-in

**Severity:** 🟡 Clarification — OpenAPI marks the response object as `additionalProperties: true`, so we modeled it loosely.

FE built `useSearch({q, limit, projectId, documentId, pageNum, scoreThreshold})` and renders results assuming each hit has `{text, score, doc_name?, page_num?, document_id?, kind?, source_id?}`. **Please confirm:**

1. **Response envelope.** OpenAPI hints at `{query, count, results: object[]}`. FE matches this. Lock it in?
2. **Per-hit fields.** Which of these are guaranteed vs optional?
   - `source_id` (string) — used as React key
   - `text` (string) — required for rendering
   - `score` (float) — required, FE sorts on it
   - `doc_name` (string)
   - `page_num` (int)
   - `chunk_index` (int)
   - `document_id` (uuid) — used to deep-link to `/documents/{id}`
   - `project_id` (uuid)
   - `kind` (string — `text` | `table` | `image` | `entity`?)
   - `collection` (string — Qdrant collection name; v1 internal)
3. **Filter behaviour.**
   - When `document_id` is passed, are results restricted to that doc only, or is it a soft boost?
   - When both `project_id` and `document_id` are passed, is `document_id` the override (FE assumes yes)?
   - `page_num` filter — exact match only, or range? FE assumes exact match.
   - `score_threshold` — minimum score, results below are dropped server-side?
4. **Default `limit`.** FE defaults to 10. If the backend caps at e.g. 50, please document so we can disable larger inputs.
5. **Highlighting.** Any chance the backend can return a `highlights: string[]` per hit (e.g. matched n-grams) so FE can bold them in the snippet? **Optional — defer if costly.**

### v2.4 Pass — Resolved by FE shipping (no backend action needed)

| Item | What FE did |
|---|---|
| Old FE assumed `/documents/{id}/full` returned PDF for `<iframe>` rendering. | Replaced with markdown fetch + page-image preview rail. See [app/(main)/documents/[documentId]/page.tsx](../app/(main)/documents/%5BdocumentId%5D/page.tsx). |
| Pre-OpenAPI `ChatRequest` used `job_id` / `doc_name`. | Replaced with `project_id` / `document_id` scope per OpenAPI. |
| `Project.updated_at` was optional in FE types. | Made required to match `ProjectOut.required`. |
| MSW handlers missing `/chat` and `/search`. | Added with realistic stubbed responses (one grounded claim + source for chat; two filterable hits for search). |
| `nav.chat` / `nav.search` flags were dark. | Flipped on by default; sidebar now shows both entries. |

---

## Communication Cadence

- **Weekly sync** — frontend brings the open items table; backend acks/resolves/defers each.
- **Per-PR backend update** — when a backend PR (BACKEND_SPEC §9 PR-1..PR-4) merges, frontend regenerates types + flips MSW handler off for that endpoint within the same week.
- **Phase boundary** — frontend updates this doc with the next phase's "Anticipated asks" promoted into "Open Items," and notes any P0–P6 modifications discovered.
- **Companion doc:** [communication_from_backend.md](communication_from_backend.md) is the canonical backend→frontend response. When a `C-NN` flips Open→Done there, the matching `B-NNN` row in this doc moves to its phase's **Resolved** subsection.

---

## Phase Sign-off Log

| Date | Event | Outcome |
|---|---|---|
| 2026-05-06 | P0 scaffold complete | All P0 deliverables on disk; CI green pending first run. |
| 2026-05-06 | Role-aware dashboard plan added | [ROLES_AND_DASHBOARDS.md](ROLES_AND_DASHBOARDS.md) v0.1 published; B-007→B-011 raised. |
| 2026-05-06 | Backend response v1.0 received | All 11 asks accepted; 5 commitments in-flight this week; commitment list §2 signed off by frontend. |
| 2026-05-06 | **v2.4 OpenAPI integration pass complete (FE)** | All v2.4 endpoints wired against [docs/backendopenapi.json](backendopenapi.json). 4 new asks raised (B-014..B-017); B-014 is a live blocker. Build green at `BUILD_ID = XDukNk6nQd7Bn67feaNIx`. |
| 2026-05-06 | **Backend response v2.5 received & reconciled** | B-014 fixed and verified (`OPTIONS /chat → 204`); B-015/B-016/B-017 answered in writing; status corrections applied for B-001/B-002/B-005/B-012/B-013 (all ✅ Resolved). Auth-shape guardrail confirmed: Supabase JS SDK owns sign-up/sign-in; backend owns `GET /auth/me` + `PATCH /auth/me/profile` only. FE already conforms. |

---

*End of Frontend → Backend Report v1.6 — 2026-05-06 (backend v2.5 reconciled; B-014..B-017 closed; only B-007/B-008/B-009/B-010 remain on the backlog).*
