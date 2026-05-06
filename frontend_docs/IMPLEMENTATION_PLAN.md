# WellSynth AI — Frontend Implementation Plan

> **Version:** 1.0
> **Date:** 2026-05-06
> **Status:** Pre-kickoff. Repo currently contains only specs — no code, no scaffolding.
> **Companion docs:** [FRONTEND_SPEC.md](FRONTEND_SPEC.md) · [BACKEND_SPEC.md](BACKEND_SPEC.md)

This document operationalizes the Frontend Spec (v2.0) into an executable, phase-wise plan. It records the modifications we are applying to the spec's stated milestones (§19 / §40.4) and the rationale for each.

---

## 1. Plan Modifications vs. FRONTEND_SPEC §19 / §40.4

The spec defines six milestones (M1–M6) with the demo headline at M3. After re-reading the backend spec we recommend the following adjustments:

| # | Change | Rationale |
|---|---|---|
| **MOD-1** | Insert a **Phase 0 — Foundations** before M1. | Spec assumes scaffolding exists; repo is empty. We need Next.js 15 + Tailwind 4 + shadcn + tokens + AppShell + design-system primitives + API client + auth shell + theming + telemetry stubs **before** any feature work, or every feature PR will re-do the same wiring. |
| **MOD-2** | Split M1 into **M1a (Shell + Upload + Library)** and **M1b (Document Workspace + Page Detail + Chat)**. | M1 in the spec is too wide for a single sprint (Dashboard + Upload + Chat + Doc Workspace + Settings). Splitting reduces merge risk and unblocks parallel work on Chat (which depends on the Evidence panel + Citation parser, both substantial). |
| **MOD-3** | Move **Auto-Summary rendering** from M3 → M1b. | Backend ships Auto-Summary first (BACKEND_SPEC §9 PR-1). It is a read-only `GET` and unlocks the Dashboard hero, Library cards, and Overview tab. Rendering it early de-risks demo polish. |
| **MOD-4** | Move **Compare rendering** from M3 → M2. | Backend Compare (PR-2) is deterministic and lands right after Auto-Summary. The Compare UI reuses Document Workspace primitives we already have at end of M1b — natural fit for M2. |
| **MOD-5** | Keep **Pre-Spud Briefing + Lessons-in-Context** as the M3 headline. | These are the two LLM-backed killer features; they deserve a dedicated milestone for design polish, empty states, citations, print/PDF, and telemetry. |
| **MOD-6** | Promote **Pinboard** to M3 (alongside Briefing/Lessons), defer **Reports composer** to M4. | Pinboard is the substrate Briefings/Lessons want ("Pin lesson," "Pin briefing"). Without it, those features feel one-shot. Reports composer (multi-pinboard PDF export) is heavier and can ship in M4 with the Export Center. |
| **MOD-7** | Frontend-stub all new backend endpoints behind a typed API client + MSW handlers from day one. | Backend ships its 4 PRs in series (Summary → Compare → Briefing → Lessons). Frontend cannot block on backend merges. We mock against the OpenAPI shapes in BACKEND_SPEC §3–§6 immediately. |
| **MOD-8** | Make **Projects** a hard "out of scope for v1" — drop the `/projects/*` routes from the build plan. | FRONTEND_SPEC §40.2 already calls this out; we're confirming the ruling and removing the stub work. Project tagging can ship later as a frontend-only chip on Document Library. |
| **MOD-9** | **Onboarding (`driver.js` tour) → M5**, not M4. | A tour requires stable selectors across screens. Doing it before M4 polish causes constant rework. Skipped on first run is fine for early demos. |
| **MOD-10** | Add an explicit **API Contract Mocks** workstream that runs in parallel with Phases 1–3 and is owned by frontend. | We turn the backend Pydantic schemas (BACKEND_SPEC §2.1, §3.2, §4.2, §5.3, §6.2) into Zod schemas + MSW fixtures. This is the single source of truth for both UI dev and contract testing. |
| **MOD-11** | Light/dark theme parity is a **per-PR gate**, not a phase. | Every screen merged must pass axe + contrast in both themes (FRONTEND_SPEC §38). Easier to enforce in code review than to retrofit at M6. |
| **MOD-12** | Treat **Sentry / PostHog / Lighthouse CI** as Phase 0 stubs (off by default), Phase 6 as the full activation. | Stubs let us emit events from day one without setup churn later. |

**Removed from v1 build plan** (per FRONTEND_SPEC §40.2 + MOD-8): Projects, Wells/Sections/Campaign, Artifact revisions, Action Board, Risks browser, Drafts queue, Audit log, Approvals, Daily Summary / Shift Handover, Offset Intelligence (graph). Pre-Spud Briefing is the demoed stand-in for offset intelligence.

---

## 2. Phase Plan (Headline)

| Phase | Name | Demoable Outcome |
|---|---|---|
| **P0** | Foundations | App boots. Login → empty Dashboard → theme toggle. Storybook for primitives. CI green. |
| **P1a** | Shell + Ingest + Library | Upload PDFs, watch live progress, browse the library grid. |
| **P1b** | Document Workspace + Chat (single-doc) | Open a doc, see Overview/Pages/Activity, ask a question, see cited evidence. **Auto-Summary rendered.** |
| **P2** | Browsers + Search + Compare | Tables/Entities/Images browsers, global Search, Compare two docs. |
| **P3** | Killer Features + Pinboard | Pre-Spud Briefing, Lessons-in-Context widget + `/lessons`, Pinboard. |
| **P4** | Reports, Export, Email, Sharing | Report composer, Export Center (PDF/MD/CSV/zip), Resend templates, share tokens. |
| **P5** | Premium Polish | Command palette, Notifications, Activity, Onboarding tour, Map (empty), Empty/Loading catalogs, copy pass. |
| **P6** | Hardening & Launch | Sentry, PostHog, Lighthouse CI, axe-core CI, perf pass, error boundaries, prod build, deployment runbook. |

Cross-cutting workstreams (run in parallel from P0):
- **API Contracts & Mocks** (MOD-7, MOD-10) — owns `lib/types.ts` + MSW handlers.
- **Design Tokens & Theming** (MOD-11) — every PR ships dark+light parity.
- **Telemetry Skeleton** (MOD-12) — `lib/telemetry.ts` no-op stub from P0.

---

## 3. Phase Detail

### Phase 0 — Foundations

**Goal:** A boring, working app shell that any feature dev can clone, run, and start building inside in under 15 minutes.

**Scope**
- Repo init: `pnpm`, Next.js 15 (App Router, RSC, Turbopack), TypeScript strict, ESLint + Prettier, Husky + lint-staged.
- Tailwind 4 config wired to FRONTEND_SPEC §3.2 / §3.2.1 token variables. `tailwind.config.ts` reads CSS vars.
- shadcn/ui init + first wave of primitives: Button, Input, Dialog, DropdownMenu, Tabs, Tooltip, Toast (Sonner), Skeleton, Badge, Card.
- Theming: `data-theme` attribute on `<html>`, pre-hydration script in `app/layout.tsx` to prevent FOUC, system / light / dark switcher persisted to `localStorage` under `ws.preferences.theme`.
- Folder skeleton per FRONTEND_SPEC §17.
- `lib/api.ts` — typed fetch client with base URL, JWT bearer header, error normalization, 401 → `/login`.
- `lib/types.ts` — initial type set mirroring backend `/chat` + `/ingest` + new endpoints (BACKEND_SPEC §2.1, §3.2, §4.2, §5.3, §6.2).
- `lib/store/` — Zustand slices stubbed: `auth`, `ui`, `chat`, `ingestion`, `notifications`.
- `lib/queries/` — TanStack Query provider + first hooks (`useJobs`, `useJob`).
- AppShell layout: sidebar (collapsible), topbar (global search placeholder, notifications bell stub, user menu), content slot, right-pane slot.
- Demo auth: `iron-session` cookie, `/api/auth/{login,logout,me}`, `/login` page, gate on `(main)` route group.
- Telemetry stub: `lib/telemetry.ts` → no-op `track()` function (PostHog wired in P6).
- Error boundary at route group level + global 500 page + offline banner stub.
- Storybook (or Ladle) for primitives — both themes, both densities.
- MSW (Mock Service Worker) wired for dev + tests; fixtures folder seeded.
- Vitest + Playwright + axe-core configured. CI: GitHub Actions runs `lint`, `typecheck`, `test`, `e2e:smoke`, `build`.
- `.env.local.example` with all keys from FRONTEND_SPEC §18.

**Out of scope for P0:** any feature route, any chat/ingest UI, any real data calls beyond `/api/auth/me`.

**Definition of Done**
- `pnpm dev` boots to login, login navigates to an empty Dashboard skeleton.
- Theme switcher works without FOUC in both directions.
- Storybook shows ≥ 10 primitives in both themes.
- CI is green on a PR that touches one component.
- README documents `pnpm dev`, `pnpm test`, `pnpm storybook`, env setup.

---

### Phase 1a — Shell + Ingest + Library

**Goal:** Get PDFs into the app and visible.

**Scope**
- **Dashboard skeleton (`/`)** — KPIStat cards (wired to `GET /ingest` aggregates), Active Jobs strip, empty state. Hero CTAs render but only "Upload" is live.
- **Ingest Studio (`/upload`)** — DropZone (drag-anywhere on page), JobList, JobCard with phase chip, progress ring, ETA, per-page counter, cost meter (sidebar). Polling via TanStack Query (`refetchInterval: 2000` while `processing`). Pause polling when tab hidden (default behavior).
- **Document Library (`/documents`)** — DocGrid + DocCard (filename, page count, ingestion date, status badge, thumbnail from page 1). Virtualization (TanStack Virtual) when count > 50. Search-as-you-type filter.
- **Settings (`/settings`)** — Preferences sub-tab only (theme, density, motion, typewriter toggle). API & Keys tab shows masked fields, no edit yet.
- **Shared:** Sidebar items light up for the routes shipped this phase. Notifications bell shows 0 (no inbox yet).
- Mock-first: every endpoint has an MSW handler with a realistic 22-page-doc fixture. Toggle to real backend via env.

**Backend dependencies:** `POST /ingest/upload`, `POST /ingest`, `GET /ingest`, `GET /ingest/{job_id}`, `GET /ingest/{job_id}/pages/{n}/image`. **All exist already.**

**Definition of Done**
- Drag a PDF, see it ingest, watch live phase chip change, click into Library.
- Refresh the page mid-ingest — state survives via polling.
- Library renders 100 mocked docs at 60 fps (perf budget §12).
- All four states (empty / loading / error / success) implemented for Upload + Library.
- A11y: tab through Upload + Library with no traps; axe clean both themes.

---

### Phase 1b — Document Workspace + Chat (single-doc) + Auto-Summary

**Goal:** The "open a document, ask a question, see citations" loop.

**Scope**
- **Document Workspace (`/documents/:jobId`)** — tabbed shell: Overview · Pages · Tables (placeholder) · Entities (placeholder) · Images (placeholder) · Activity. Header: filename, status, page count, ingestion date, "Chat about this document," "Export" menu (disabled stub).
  - **Overview tab** ships fully: `AutoSummaryCard` + `KeyFactsPanel` (MOD-3) reading from `GET /documents/{job_id}/summary`. Empty-state fallback if backend hasn't backfilled yet.
  - **Pages tab** ships fully: thumbnails rail (lazy), main pane = image + markdown split with draggable splitter, zoom/pan controls.
  - **Activity tab** ships fully: ingestion run log from manifest.
- **Page Detail (`/documents/:jobId/pages/:n`)** — split view, prev/next nav, "Open in new tab," "Copy page link," "Pin this page" (button stub until P3).
- **Chat (`/chat`, `/chat/:sessionId`)** — three-pane on ≥ 1440 px, two-pane fallback.
  - Composer with scope chip (workspace / document — multi-doc deferred to P2/P3).
  - `MessageList` with `MessageBubble`, optimistic user message, skeleton bot bubble.
  - `MarkdownRenderer` with citation parser (FRONTEND_SPEC §21) — `[Sn]` → `CitationChip`.
  - `EvidencePanel` with `EvidenceCard` variants (text / table / image / page). Hover-sync between citation and card.
  - `ConfidenceBar`, `StatusBanner` (partial / no_evidence / clarification_requested).
  - Per-message actions: copy, copy-with-citations, regenerate, "Why this answer?" debug drawer. Pin/share are stubs.
  - Typewriter effect (toggleable, default on per Settings).
- Suggested questions row in empty Chat state and in Overview tab — generated client-side from doc's key facts (FRONTEND_SPEC §29.3).

**Backend dependencies:** `POST /chat`, `GET /ingest/{job_id}/pages/{n}/image{,/crop}`, `GET /documents/{job_id}/summary` (BACKEND_SPEC PR-1; mock until merged).

**Definition of Done**
- Full Upload → Open doc → Overview shows summary + key facts → Pages tab navigable → start chat → ask question → answer renders with chips → click chip scrolls evidence card and pulses.
- Both `partial` and `no_evidence` states have correct UI.
- Print stylesheet for Page Detail.
- Perf budget: chat keystroke → render < 16 ms; page thumbnail batch first-paint < 800 ms for 20 thumbs.
- Telemetry stub fires `chat.sent`, `chat.answered`, `chat.cited_clicked`, `summary.viewed`.

---

### Phase 2 — Browsers + Search + Compare

**Goal:** "I can find anything, in any document, and diff two of them."

**Scope**
- **Tables tab + `/tables` Browser** — TanStack Table for browsing, modal preview with sort/filter inside, CSV/JSON export buttons (frontend converts MD → CSV).
- **Entities tab + `/entities` Browser** — facet sidebar (type / unit / value range / doc), grouped rows, click-through to source page, CSV export.
- **Images tab + `/images` Browser** — grid by `image_type`, lightbox modal with description + parent page link.
- **Search (`/search`)** — global search bar (also bound to `/` keyboard shortcut), filters as pill row, tabbed results (All / Text / Tables / Entities / Images), score-bar visualization, "Save search" + "Search history" panels (localStorage).
- **Compare (`/documents/:jobA/compare/:jobB`)** — three-region layout (FRONTEND_SPEC §39.4), `CompareHeader` with swap, `CompareRow` with delta chips, `CompareCallout` for severity, `FormationStripChart` (Recharts), "Ask about this comparison" button (seeds chat with both jobs in scope).
- Compare entry points: from any Document Workspace header ("Compare with…" picker), from Library multi-select.
- Multi-doc chat: scope chip allows multi-select; for workspace scope, omit `job_id`/`doc_name` (FRONTEND_SPEC §29.1).

**Backend dependencies:** `GET /search` (exists), `POST /compare` (BACKEND_SPEC PR-2; mock until merged).

**Definition of Done**
- Search returns results with all 4 result kinds rendered correctly.
- Compare two real ingested docs in < 500 ms (uses backend cache); UI renders deltas, severity colors, callouts; print stylesheet works.
- All browsers virtualize beyond 200 rows.
- Empty states catalog covered for every browser variant.

---

### Phase 3 — Killer Features (Briefing + Lessons) + Pinboard

**Goal:** The demo headline. The two features that turn this into a drilling tool, plus the pinboard substrate that makes them sticky.

**Scope**

**Pinboard (`/pinboard`)**
- BoardSwitcher, Pinboard grid, PinCard with kind icon (answer/evidence/page/table/entity/search), drag-to-reorder, rename/recolor/reorder boards.
- "Pin" actions wired across Chat, Page Detail, Evidence panel, Search results, Tables/Entities/Images browsers, Briefings, Lessons.
- Pin payload typed per kind (FRONTEND_SPEC §28). Persisted to `localStorage` under `ws.pinboard`.
- "Chat with this board" button — sends single chat request scoped to most-pinned `doc_name`, prefixes message with system note listing pinned page numbers (per spec §28 v1 limitation).

**Pre-Spud Briefing**
- `/briefings` list (history) and `/briefings/new` form (React Hook Form + Zod, all fields per FRONTEND_SPEC §39.1).
- `/briefings/:id` premium report layout: `BriefingHeader`, `RiskCard` (frequency bar, duration stat, IADC chip, root-cause line, "what worked," citations row), `HazardRow`, `MitigationItem`, `OffsetWellTable`.
- Right rail: same Evidence/citation panel used by Chat — citations clickable to source page.
- "Export PDF" via `@react-pdf/renderer` (server-rendered for fidelity).
- "Email briefing" via Resend — wired in P4; button stub here.
- "Pin briefing" → goes to Pinboard.
- Print stylesheet for `/briefings/:id`.

**Lessons-in-Context**
- `/lessons` standalone browser with `LessonFilterBar` (formation / op type / section), `LessonCard`.
- `<LessonsInContext>` widget — context-aware right-rail card. Mounts in:
  - Document Workspace (right rail)
  - `/briefings/:id` (right rail)
  - Chat session when scope has a clear single doc context
- Widget calls `POST /lessons/applicable` with derived context, renders 3–5 ranked cards. Subtle accent border when top score ≥ 0.85. Collapses to "3 lessons applicable" when dismissed; reappears on context change.
- Telemetry: `lessons.surfaced`, `lessons.engaged`.

**Backend dependencies:** `POST /briefings/pre-spud`, `GET /briefings`, `GET /briefings/{id}` (BACKEND_SPEC PR-3); `POST /lessons/applicable` (BACKEND_SPEC PR-4). Mocks first.

**Definition of Done**
- 3-min walkthrough demo passes:
  1. Open Library → click doc → see Auto-Summary + KeyFacts + lessons widget.
  2. Open `/briefings/new` → fill form → "Generate" → premium report renders with ≥ 3 NPT risks, ≥ 1 hazard, ≥ 1 mitigation, all citations clickable.
  3. Pin one briefing card to Pinboard → see it in `/pinboard`.
  4. Compare two wells → "Ask about this comparison" → chat thread with both in scope.
- Print/PDF for briefings is byte-for-byte clean (no clipped cards, no missing citations).
- Empty states for: zero offsets matched, zero lessons classified, no docs ingested.

---

### Phase 4 — Reports, Export Center, Email, Sharing

**Scope**
- **Reports (`/reports`)** — `ReportComposer` (pick pinboards + sessions + tables → preview → export), saved reports list with re-export, `ReportPreview`.
- **Export Center** — `DownloadButton` dropdown wired everywhere with applicable formats (FRONTEND_SPEC §27): PDF, Markdown, CSV, JSON, evidence pack `.zip`.
- **Email (Resend)** — Next.js API routes `/api/email/{share,export,digest}`, React Email templates: `share.tsx`, `export.tsx`, `digest.tsx`, `failure-alert.tsx`, `feedback-receipt.tsx`. `RESEND_API_KEY` server-only.
- **Share tokens** — `/api/share/{issue,resolve}` with HMAC-signed JWT (`SHARE_TOKEN_SECRET`), TTL options (24 h / 7 d / 30 d), revocation list in localStorage. `/share/:token` server-rendered read-only view of chat or report with watermark.
- **Settings:** Notifications sub-tab activated (per-event opt-ins), Workspace tab (name, logo, accent override), Profile tab (signature for emailed reports).

**Definition of Done**
- Compose a report from 1 pinboard + 1 chat → export PDF → output is identical to print preview.
- Issue a 7-day share token from a chat → open in incognito → read-only view renders with watermark; clicking "expire" in original session breaks the share.
- Resend test mode delivers all 5 templates without leaking the API key to the browser bundle.

---

### Phase 5 — Premium Polish

**Scope**
- **Command Palette (`cmdk`)** — Cmd/Ctrl+K everywhere; sections per FRONTEND_SPEC §23 (Go to / Recent / Actions / Search passthrough / Chat shortcut).
- **Keyboard shortcuts** — full FRONTEND_SPEC §23 table, behind `keyboardNav` flag for Vim-style nav. `?` cheat sheet modal.
- **Notifications Center (`/notifications`)** — inbox + bell unread count. Triggers per FRONTEND_SPEC §26 (`ingest.completed`, `ingest.failed`, `share.opened`, `report.ready`, `digest.weekly`).
- **Activity Feed (`/activity`)** — timeline of frontend-tracked + backend-derivable actions, filters.
- **Onboarding (`/onboarding`)** — 3-step wizard, sample doc loader, `driver.js` tour with 5 stops (deferred from P3 per MOD-9).
- **Map View (`/map`)** — MapLibre + maptiler dark style. Renders gracefully empty when no coordinates extracted (the demo state).
- **Empty States Catalog (FRONTEND_SPEC §24)** — every "zero" screen audited and polished.
- **Loading & Skeleton Catalog (FRONTEND_SPEC §25)** — every wait state replaced with content-shaped skeletons.
- **Copy pass** — every user-facing string reviewed against §35 voice guide.
- **Help (`/help`)** — shortcuts cheat sheet, links, "Send feedback" modal (Resend).

**Definition of Done**
- Command palette covers 100% of routes + 8 actions.
- Onboarding tour completes without selector errors after a fresh `localStorage.clear()`.
- All §24 + §25 entries covered; visual diff snapshots committed.

---

### Phase 6 — Hardening & Launch

**Scope**
- **Sentry** integration (browser + Next.js server), source maps uploaded in CI.
- **PostHog** activated, all events from FRONTEND_SPEC §14 + §39.8 firing with no PII.
- **Lighthouse CI** budgets enforced per FRONTEND_SPEC §12 — fail PR if regression > 10%.
- **axe-core in Playwright CI** — zero serious/critical on every route, both themes.
- **Visual regression suite** — Playwright screenshots per FRONTEND_SPEC §15 list.
- **Perf pass** — bundle analyzer review, dynamic-import all heavy routes (Map, MarkdownRenderer, ReportComposer, CommandPalette), tree-shake icons audit, virtualization audit.
- **Error boundaries** — per route group; "Send report" hooks to Sentry user feedback.
- **Offline banner** + cached read paths via service worker (read-only fallback).
- **i18n scaffolding** — wrap all strings in `t("key")` from `next-intl` (English-only catalog ships).
- **Production build & deployment runbook** — Vercel and Docker paths both documented; env matrix per `.env.local.example`.

**Definition of Done**
- All §38 "Definition of Premium" boxes ticked on every shipped route.
- Lighthouse: Performance ≥ 90, A11y ≥ 95 on dashboard, library, doc workspace, chat, briefing report.
- Demo script rehearsed end-to-end with real backend in dark + light modes without a single console error.

---

## 4. Cross-Cutting Workstreams

### 4.1 API Contracts & Mocks (owner: frontend, runs P0 → P3)

- `lib/types.ts` mirrors backend Pydantic models from BACKEND_SPEC §2.1, §3.2, §4.2, §5.3, §6.2 verbatim.
- Zod schemas for every request/response — used both by RHF forms and runtime validation in dev.
- MSW handlers under `tests/mocks/handlers/` with realistic fixtures for: 1 ingested doc, 14-offset briefing corpus, 3 pre-classified lessons, 2-doc compare.
- Toggle real-vs-mock via `NEXT_PUBLIC_USE_MOCKS=true`.
- Snapshot tests assert mock responses match Zod schemas (so when backend updates schemas we fail fast).

### 4.2 Design Tokens & Theming (owner: frontend, runs every PR)

- All colors / shadows / motion read from CSS vars defined in `styles/globals.css` per FRONTEND_SPEC §3.2 / §3.2.1 / §3.3.
- shadcn primitives forked once with token-bound styles, never with hex colors.
- Per-PR checklist (added to PR template):
  - [ ] Renders correctly in System / Light / Dark
  - [ ] axe clean on changed routes in both themes
  - [ ] Tabular nums where numeric
  - [ ] Empty + Loading + Error + Success states
  - [ ] Visible focus rings
  - [ ] Reduced-motion respected

### 4.3 Telemetry Skeleton (owner: frontend, P0 → P6)

- `lib/telemetry.ts` exports `track(event, props)` — no-op in P0, real in P6.
- Every feature PR adds the events listed in FRONTEND_SPEC §14 / §39.8 even before PostHog is wired (so activation in P6 is one config change).

### 4.4 Backend ↔ Frontend Sync

- Each backend PR (BACKEND_SPEC §9) ships an OpenAPI snapshot. Frontend regenerates types + bumps mock fixtures the same week.
- Order: PR-1 (Summary) → P1b. PR-2 (Compare) → P2. PR-3 (Briefing) → P3. PR-4 (Lessons) → P3.
- If backend slips, frontend keeps shipping against mocks and swaps the env flag at integration time.

---

## 5. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Backend PR slip on Briefing/Lessons | Med | High (demo headline) | MSW mocks + fixtures keep frontend velocity. Demo script can fall back to mock mode in worst case. |
| Citation hover-sync perf on long answers | Low | Med | Virtualize evidence panel beyond 30 cards; throttle hover handler. |
| PDF export fidelity (briefing report) | Med | High | Use `@react-pdf/renderer` server-rendered; build a snapshot test on a golden briefing JSON. |
| Theme parity drift over 6 phases | High | Med | Per-PR checklist + axe in CI block merge. |
| Pinboard "chat with this board" v1 limitation (single most-pinned doc) | Low | Low | Document clearly in UI tooltip; backend extension flagged in spec already. |
| LocalStorage quota exhaustion (pinboard + activity + saved searches) | Low | Med | Auto-evict oldest activity; surface a soft warning toast at 80% quota. |
| Backend lacks WebSocket → no live chat streaming | Known | Low | Typewriter effect already specified as the demo polish; SSE flagged as future. |

---

## 6. Open Questions (need answers before P3 starts)

1. **Briefing form autocomplete for formations/fields** — does backend expose a list endpoint, or do we derive from `wellsynthai_entities` via existing `GET /search` calls? *Recommendation:* derive client-side for v1; flag for proper endpoint later.
2. **Pinboard "chat with board" cross-doc fidelity** — confirm the §28 v1 limitation (single doc + system prefix) is acceptable for demo. If not, it becomes a backend ask before P3.
3. **Sample data for onboarding** — the `GSWA_Harvey_1_WCR.pdf` is referenced; confirm it lives in backend `data/output` of demo deploys, otherwise we need a frontend-bundled fallback.
4. **Resend domain & sender** — `EMAIL_FROM=noreply@wellsynth.ai` requires DNS setup. Track separately so it's not a P4 surprise.
5. **Share token revocation** — frontend-only `localStorage` revocation list works for single-browser demo, but breaks if shared from another device. Acceptable for v1?
6. **Map basemap tiles** — MapLibre + maptiler implies a maptiler key in some scenarios. Confirm the open style we'll use needs no token.

---

## 7. Sequencing Snapshot

```
P0  ─────► P1a ─────► P1b ─────► P2 ─────► P3 ─────► P4 ─────► P5 ─────► P6
                       ▲           ▲          ▲
                       │           │          │
            backend PR-1     PR-2      PR-3 + PR-4
            (Summary)     (Compare)  (Briefing + Lessons)

Cross-cutting (P0→P6): Contracts/Mocks · Theming · Telemetry · CI
```

P3 is the demo headline. P4–P6 are the polish that turns it from "killer demo" into "premium product."

---

*End of Implementation Plan v1.0.*
