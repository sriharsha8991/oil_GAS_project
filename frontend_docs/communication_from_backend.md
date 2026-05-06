# WellSynth AI — Backend → Frontend Communication

> **Purpose:** Living document the **backend team** uses to respond to the frontend team. Mirrors [BACKEND_REPORT.md](BACKEND_REPORT.md) (which is frontend → backend). Forward-replace this doc whenever a backend commitment changes status.
>
> **How to read:** §1 is the executive summary. §2 is the canonical commitment list (versioned action items). §3 answers every open question raised in `BACKEND_REPORT.md`. §4 is per-phase commentary. §5 covers the role-aware dashboard endorsement. §6 lists what we want from frontend in return.
>
> **Last updated:** 2026-05-06 — initial response to frontend docs v1.1.
> **Owner:** Backend team.
> **Status:** Awaiting frontend sign-off on §2 commitment list.

---

## 1. Executive Summary

The frontend team's docs (BACKEND_REPORT, IMPLEMENTATION_PLAN, ROLES_AND_DASHBOARDS) are **accepted in full**. No objections to MOD-1 through MOD-12. The role-aware dashboard direction and §5 visual refinement are endorsed.

**TL;DR for the frontend team:**

- **All 11 backend asks (B-001 → B-011) accepted.** 5 land this week, 3 land alongside specific PRs, 2 land before demo, 1 deferred post-demo.
- **One critical heads-up:** `doc_name` is **not stable** across re-ingestion of the same PDF (intentional). Group by `filename` for any cross-ingest aggregation. Details in [§3.4](#34-doc_name-is-not-deterministic).
- **One existing bug we'll fix:** current backend CORS is `allow_origins=["*"]` with `allow_credentials=True`, which browsers reject per spec. Tightening to env-driven allow-list this week.
- **One spec extension we volunteer:** `/chat` will gain `job_ids: string[] | null` in PR-3 (removes the §28 v1 single-doc limitation for "Chat with this board").
- **One persona suggestion:** keep **Wellsite Supervisor** on the roadmap as the operational counterpart to Drilling Engineer (planning vs. live ops). Defer for v1; revisit post-demo.

Build order remains **PR-1 → PR-2 → PR-3 → PR-4** aligned to **P1b → P2 → P3 → P3**, exactly as IMPLEMENTATION_PLAN §7 sequences it.

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
| **C-27** | Server-side `/chat/sessions/{id}` history | Sessions live client-side in v1 |
| **C-28** | Webhook for ingestion completion → email digest | Frontend handles email via Resend; no backend hook needed |

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

1. **Sign off on §2 commitment list** — confirm acceptance and target windows so it becomes the contract.
2. **Confirm doc_name grouping mental model** ([§3.4](#34-doc_name-is-not-deterministic)) — frontend will group cross-ingest aggregations by `filename`, not `doc_name`.
3. **Lock `applicability.reasons` enum** to the four values in [§3.7](#37-locked-enums) before P3 starts.
4. **Lock `empty_state_reason` enums** before P3 starts.
5. **Reference the existing static UI** ([app/static/chat.html](../app/static/chat.html), [app/static/ingest.html](../app/static/ingest.html)) as a working reference for ambiguous API behavior. Not a design reference — a behavioral one.
6. **Flag any of C-19 / C-20 / C-21** if needed earlier than P5. They're easy to bring forward.

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

*End of Backend → Frontend Communication v1.0 — 2026-05-06.*
