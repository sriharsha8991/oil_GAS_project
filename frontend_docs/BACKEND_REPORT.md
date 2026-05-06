# WellSynth AI — Frontend → Backend Report

> **Purpose:** Living document the frontend team uses to communicate to the backend team. Updated at the **end of every phase** of [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). Records open questions, contract clarifications needed, schema mismatches found, modification requests, and integration milestones.
>
> **How to read:** Each phase has its own section. The **Open Items** table is the canonical to-do for backend. Resolved items move to the **Resolved** subsection of the relevant phase, with the resolution noted.
>
> **Last updated:** 2026-05-06 — v1.4. Added **B-013** (per-user document ownership / personalised corpus) per product decision: every job, briefing, lesson, chat session, and pinboard becomes scoped to the owning user; cross-user visibility is opt-in (sharing) only.

---

## 🟢 Backend Response Received — read this first

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
| P1b — Document Workspace + Chat + Auto-Summary | Not started | — |
| P2 — Browsers + Search + Compare | Not started | — |
| P3 — Briefing + Lessons + Pinboard | Not started | — |
| P4 — Reports + Export + Email + Sharing | Not started | — |
| P5 — Premium Polish | Not started | — |
| P6 — Hardening & Launch | Not started | — |

## Top Priorities — status snapshot

All 5 top-priority items have been accepted by backend. Status reflects backend commitment IDs from [communication_from_backend.md §2](communication_from_backend.md#2-backend-commitment-list-canonical):

| # | Ask | Backend commitment | Window | Status |
|---|---|---|---|---|
| 1 | B-001 — OpenAPI export | C-01 | This week | 🟡 In progress |
| 2 | B-002 — CORS allow-list | C-03 | This week | 🟡 In progress |
| 3 | B-008 — `/entities/distinct` | C-13 | With PR-3 | ⏳ Scheduled |
| 4 | B-005 — real `JobManifest` sample | C-02 | This week | 🟡 In progress |
| 5 | B-007 — `/admin/stats` | C-20 | Pre-demo (P5) | ⏳ Scheduled — frontend OK with P5 timing |
| 6 | **B-012 — Accounts + roles in DB** | *new* | This week / next | 🔴 NEW — awaiting backend response |
| 7 | **B-013 — Per-user document ownership** | *new* | This week / next | 🔴 NEW — awaiting backend response |

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

---

*End of Frontend → Backend Report v1.2 — 2026-05-06 (backend response received & acknowledged).*
