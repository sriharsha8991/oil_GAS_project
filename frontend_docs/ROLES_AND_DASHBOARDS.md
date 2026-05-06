# WellSynth AI — Roles, Dashboards, and Visual Direction

> **Version:** Draft 0.1 — for review
> **Date:** 2026-05-06
> **Status:** Planning. No code changes yet.
> **Companion docs:** [FRONTEND_SPEC.md](FRONTEND_SPEC.md) · [BACKEND_SPEC.md](BACKEND_SPEC.md) · [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) · [BACKEND_REPORT.md](BACKEND_REPORT.md)

This document answers two questions raised in review:

1. **Should the dashboard be role-aware?** (Yes — and here's what each role needs.)
2. **The current look feels generic / "AI-made." How do we make it premium?** (Analysis + concrete direction.)

It is a **proposal**. Nothing is implemented. After sign-off the agreed parts fold into [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) and the backend asks land in [BACKEND_REPORT.md](BACKEND_REPORT.md).

---

## 1. Why role-aware dashboards

A single homepage cannot serve a Super Admin, a Drilling Engineer, and an Asset Manager well. Each lands looking for different evidence:

- A **Super Admin** wants to know the platform is healthy.
- A **Drilling Engineer** wants to know the next well's risks.
- An **Asset Manager** wants to know the portfolio's deltas this week.

Forcing them into one mosaic produces what we have today: an honest-but-undifferentiated KPI strip that nobody actually uses. The correct pattern is one **shell** with a **role-resolved primary view**, plus the ability to switch roles (with permissions) for users who wear multiple hats.

### 1.1 Roles we will support in v1 (demo)

| ID | Role | Persona one-liner |
|---|---|---|
| `super_admin` | **Super Admin / Ops** | Keeps the platform alive; cares about ingestion health and spend. |
| `drilling_engineer` | **Drilling Engineer** | Plans and de-risks the next well using offset history. |
| `geologist` | **Geologist / Geoscientist** | Reads formations, tops, logs across wells. |
| `asset_manager` | **Asset Manager / Team Lead** | Owns a portfolio of wells; cares about deltas and exceptions. |
| `qa_steward` | **Document QA / Steward** | Watches extraction quality; fixes what the model missed. |

**Deferred (post-demo, listed for completeness):** Reservoir Engineer, Wellsite Supervisor, Completions Engineer, HSE Lead, External Partner (read-only share). Their views can compose from the same blocks once the v1 framework lands.

### 1.2 Single tenant, multi-role

Demo backend has no multi-user model. We treat **role** as a frontend preference saved in `localStorage` under `ws.preferences.role`. The role switcher is in the user menu. No data is gated by role — every user can see everything; the role just changes which **dashboard view** is the default. This is honest demo behavior and cleanly upgrades later when backend introduces real RBAC.

---

## 2. High-value content per role

For each role I list the **single sentence the dashboard must answer in 3 seconds**, then the blocks that earn their place.

### 2.1 Super Admin / Ops

> *"Is the platform healthy and what's it costing me?"*

| Block | Data source | Why |
|---|---|---|
| **Ingestion health board** | `GET /ingest` aggregated | Counts by status (queued / processing / completed / failed) with sparklines for the last 7 days. |
| **Active jobs ticker** | `GET /ingest` poll | Live phase chip + ETA per job. The same JobCard from the Upload page, in a denser row form. |
| **Spend tracker** | manifest `cost_usd` + `tokens` | Today / 7d / 30d USD spend, broken down by Flash / Pro / embed. Sparkline + delta vs prior period. |
| **Model & tool stats** | manifest `tool_calls_made` (chat) + per-page model used (manifest extension) | "% pages escalated to Pro," "avg tool calls per chat," "p95 ingestion latency." |
| **Failure feed** | manifest `error` filter | Last 10 failed jobs with retry button — one-click triage. |
| **Storage & index** *(deferred — needs backend)* | new `/admin/stats` endpoint | Qdrant points count per collection, disk used, cache hit rate. |
| **Audit-ish event stream** | local activity feed + future `/admin/events` | Logins, exports, share-link issuances. |

**Backend asks (new):** A small `/admin/stats` aggregation route. Cheap — just sums the manifests already in Redis. Tracked as **B-007** below.

### 2.2 Drilling Engineer

> *"What should I worry about on the next well I drill?"*

| Block | Data source | Why |
|---|---|---|
| **Active well of interest** | local pinned doc | The doc the engineer is currently working from, prominent at the top with `KeyFactsPanel` + auto-summary. |
| **My briefings** | `GET /briefings` filtered to "mine" | The last 3 pre-spud briefings the user generated, with NPT-risk top-3 inlined. |
| **Lessons applicable now** | `POST /lessons/applicable` with the active doc's context | The §39.2 widget, but at dashboard scale — 5 cards, score-sorted. |
| **Risk radar across portfolio** | aggregate over recent docs' `key_facts.npt` | "Top 5 NPT categories across last 14 wells" with frequency bar. Pure aggregation over auto-summary outputs (no new endpoint). |
| **Recent compares** | local history | Last 3 `/compare` views, with the headline delta restated. |
| **Suggested questions** | derived from active doc | Existing pattern from §29.3, surfaced on the dashboard. |

**Backend asks (new):** An optional `GET /briefings?owner=…` filter (owner is frontend-tracked metadata; backend can ignore it for v1 since we cache briefings client-side too). No new data needed — this is the **strongest 80/20 case** for role-aware UX because all four killer features feed it.

### 2.3 Geologist

> *"How do these wells line up stratigraphically?"*

| Block | Data source | Why |
|---|---|---|
| **Formation strip rail** | entity collection, type=formation_top | Horizontal mini-strips of 5 recent wells on a shared TVD axis — visual stratigraphy at a glance. |
| **Formation prevalence** | aggregate over key_facts.formations | Bar chart of which formations appear in how many ingested wells. |
| **Cross-section image gallery** | image collection, image_type=cross_section | Top 6 cross-sections, click → lightbox. |
| **Map (sparse demo)** | entities lat/lon | The map view, dashboard-sized, with empty state when coords missing. |
| **Recent log plots** | image collection, image_type=log_plot | Latest 8 log-plot images with parent-page link. |
| **Search shortcuts** | saved searches | "All formation tops," "All lithology calls." |

**Backend asks (new):** None. All these read existing entity/image collections — though see B-008 below for a `/entities/distinct` helper (also needed by Briefings autocomplete).

### 2.4 Asset Manager

> *"What changed across my wells this week?"*

| Block | Data source | Why |
|---|---|---|
| **Portfolio table** | `GET /ingest` + summaries | One row per well: doc name, TD, spud date, NPT total, top NPT cause, last activity. Sortable. |
| **This-week delta** | derived | Wells added, briefings generated, exports issued, share links opened. |
| **Outliers strip** | rule-based over key_facts | "Wells with NPT > 50 h," "Wells missing TD," "Wells with rare formations." |
| **Cost-of-knowledge** | `cost_usd` aggregated | "We've spent $X across N docs." Same data as admin spend, framed for business. |
| **Recently shared** | local share-token list | Who got which read-only link. |
| **Quick compose** | UI shortcut | One click → "Generate weekly digest" → Resend email (P4). |

**Backend asks (new):** None for demo. Aggregations are fine on the client side until volume grows.

### 2.5 Document QA / Steward

> *"Where is the model failing on extraction quality?"*

| Block | Data source | Why |
|---|---|---|
| **Low-confidence extractions** | manifest + summary's missing key_facts | Wells where `key_facts.td_md_m` is missing or marked low-confidence. Click to open the doc and verify. |
| **Pages that escalated** | manifest per-page model log | Which pages went Flash → Pro and why. |
| **Empty briefings/lessons** | `empty_state_reason` from briefing/lessons endpoints | Where the corpus didn't yield anything — backlog for ingestion improvement. |
| **Re-ingest queue** | local UI | "Mark for re-ingestion" pinboard equivalent. |
| **Schema drift watch** | telemetry | When backend response shapes diverge from frontend Zod, surface here. |

**Backend asks (new):** Manifest needs to expose **per-page model used** and **per-key-fact confidence** for this view to be useful. Tracked as **B-009** below.

---

## 3. Dashboard architecture

### 3.1 One shell, role-resolved primary block

We do **not** build five separate routes. We build:

```
app/(main)/page.tsx                  ← role router
components/dashboards/
  blocks/                            ← reusable, role-agnostic blocks
    IngestionHealth.tsx
    SpendTracker.tsx
    ActiveJobsTicker.tsx
    FailureFeed.tsx
    KeyFactsHero.tsx
    BriefingDigest.tsx
    LessonsApplicable.tsx
    RiskRadar.tsx
    FormationStripRail.tsx
    PortfolioTable.tsx
    OutliersStrip.tsx
    LowConfidenceExtractions.tsx
    PagesEscalated.tsx
    ...
  layouts/
    SuperAdminDashboard.tsx
    DrillingEngineerDashboard.tsx
    GeologistDashboard.tsx
    AssetManagerDashboard.tsx
    QaStewardDashboard.tsx
```

Each layout is a thin composer over the same blocks. Adding a sixth role later is a 30-line file.

### 3.2 Role switcher

- Lives in the user menu (top-right) — not in the sidebar (sidebars are for navigation, not preferences).
- Persists to `ws.preferences.role`.
- Switching is instant; no reload. Telemetry event `dashboard.role_switched`.
- Each layout tracks `dashboard.viewed { role }` on mount.

### 3.3 First-run heuristic

On first login (no `ws.preferences.role` set yet) we ask once, inline on the empty dashboard:

> *"Which view should we open by default? **Drilling Engineer** · Geologist · Asset Manager · QA Steward · Super Admin"*

Not a modal, not a stepper — a single sentence with five chips. Pick one or skip (defaults to Drilling Engineer for the demo). Picks can be changed later from the menu. This is the *opposite* of a multi-step wizard and respects the brand voice (§35).

### 3.4 Block contract

Every block is a component with this shape:

```tsx
interface DashboardBlockProps {
  density: "comfortable" | "compact";
  // Optional scope override for blocks that aggregate the corpus
  scope?: { jobIds?: string[]; field?: string };
}
```

Blocks own their own loading/empty/error state. They are **never empty by default** in the demo — when data is missing, they render a one-line empty state with an action ("Upload your first PDF" / "Generate a briefing"), not a hidden card. Hiding cards on missing data leaves layouts feeling broken.

### 3.5 Permissions (placeholder)

For demo, every role can see every block. The block component contract takes a `requires?: Capability[]` prop that today returns `true` for any user. When backend RBAC lands, this is the single seam to gate by capability.

---

## 4. Backend additions to support roles

These slot into [BACKEND_REPORT.md](BACKEND_REPORT.md) (added in §5 below).

| ID | Ask | For role(s) | Effort | Priority |
|---|---|---|---|---|
| **B-007** | `GET /admin/stats` — aggregated counts/spend/sparkline data. Pure read over manifests in Redis. Single-tenant for demo. | Super Admin | Small | Medium (demo polish) |
| **B-008** | `GET /entities/distinct?type={formation\|field}` — autocomplete + Geologist's formation prevalence chart + Briefing form. | Geologist, Drilling Engineer | Small | High (Briefing form depends on it — BACKEND_REPORT P3 already flagged this) |
| **B-009** | Manifest extension: `pages_meta[]` with `{ page_num, model_used, escalated, confidence }` and `key_facts` confidence flags. | QA Steward | Medium | Low (deferable; QA dashboard can ship without it and just show "missing facts" using `null` heuristics) |
| **B-010** | `GET /briefings?owner=<email>` filter (or echo back user-supplied `owner` field on creation). | Drilling Engineer | Trivial (frontend-tracked) | Low |
| **B-011** | Optional: `GET /admin/events` audit stream (logins, exports, shares). Out of scope for demo, listed for roadmap. | Super Admin | Medium | Defer |

**Frontend can ship all five role dashboards without B-009 and B-011.** B-007 and B-008 are the only "hard wants" for a polished demo.

---

## 5. Visual direction — making it look like industrial software, not AI demo

The honest critique: the current dashboard reads as "AI-made" because of seven recurring patterns we should drop or temper.

### 5.1 What's making it feel generic (and what to do)

| Pattern in current draft | Why it reads as AI demo | Replacement |
|---|---|---|
| Hero gradient + welcome greeting | Looks like a SaaS landing page, not a workstation | Flat info-bar with breadcrumb · last-sync time · active workspace name. No greeting. |
| 6-up KPI strip with equal weight | Defaults that say nothing; classic "AI dashboard" | Asymmetric: **one** primary metric (largest), 2–3 secondary, the rest tucked behind a disclosure. Different per role. |
| Round, glossy cards with gradients on top | Consumer SaaS aesthetic | Flat cards, hairline border, **no card-top gradient**. Reserve gradients for the brand mark only. |
| Card hover lift translate-y | Toy-like | 1-px border accent on hover, no transform. |
| Lucide icons everywhere | Too generic | Lucide stays for chrome; **domain icons** (mini SVGs for well, formation strip, NPT bar, casing program) for the data side. |
| Round 12 px corners on every surface | Mood: app store. Industrial software is sharper. | Cards 8 px, dense rows 6 px, pills only at 999. |
| All-text content blocks | Could be any product | **Charts and tables dominate.** Recharts lines/bars/strips. Always show units. Always tabular numerals. |

### 5.2 Proposed visual update (token-level — keeps light/dark parity)

```css
/* DARK — refined */
--bg:           #0a0c10;   /* slightly cooler/deeper */
--bg-elev-0:    #0e1116;
--panel:        #12151b;   /* less plastic than #14171d */
--panel-2:      #181c24;
--panel-3:      #1f242e;
--border:       #232832;   /* more visible hairlines for density */
--border-soft:  #1a1e26;
--accent:       #5b8cff;   /* unchanged */
--accent-line:  rgba(91, 140, 255, 0.55);   /* NEW — for chart strokes */

/* LIGHT — refined */
--bg:           #f4f6f9;   /* slightly cooler */
--bg-elev-0:    #ffffff;
--panel:        #ffffff;
--panel-2:      #f1f3f7;
--panel-3:      #e8ecf2;
--border:       #d9dee6;   /* darker for industrial feel */
--border-soft:  #e8ebf0;
--accent:       #234bbe;   /* deeper, more "engineering blue" */
--accent-line:  rgba(35, 75, 190, 0.65);
```

Why these moves:

- **Cooler/deeper bg**: signals tooling, not consumer.
- **More visible borders**: dense data UIs need hairlines; current `--border` washes out.
- **Sharper accent in light mode**: `#234bbe` is closer to Bloomberg/Petrel/Techlog territory than the friendlier `#2f5fe0`.

### 5.3 Typography move

Keep Inter for body. Add **JetBrains Mono** (already in stack) for:
- All numeric data values in tables and KPIs (we already have `tabular-nums`, but mono outright signals "this is a measurement").
- Source IDs in citations (`S1`, `S2`).
- Job IDs, file hashes.

This single move makes the app read as "instrument," not "marketing site."

### 5.4 Density default

Switch the default from `comfortable` to **`compact`** for the dashboards. Comfortable becomes the explicit toggle for users who want airier layouts. Drilling engineers spend their day looking at dense logs and tabular DDRs — airy is uncomfortable for them.

### 5.5 Chart conventions

- **No filled areas** by default — strokes only. Fills imply marketing.
- **Reference lines** for important thresholds (e.g., NPT 24h / 72h bands) — labeled.
- **Y-axis units always visible**, never hidden behind tooltips.
- **Categorical bars** use `--accent` only; semantic colors (`--ok` / `--warn` / `--bad`) reserved for severity, not category.
- **Sparklines** use `--muted` stroke + `--accent` for the latest segment.

### 5.6 Empty state tone shift

Current empties are friendly: "Your workspace is empty." For an engineering tool: keep them, but lose any whimsy. Stick to **state + action**:

```
No documents ingested.
[Upload PDFs]
```

Two lines. No illustration on the dashboard (illustrations stay on the bigger empty screens like Pinboard or Reports).

### 5.7 What we keep from the spec

The full §3 design system stays — tokens, type scale, motion timings, accessibility rules, the 4-px spacing base. This visual update is a **refinement, not a rewrite**, and it preserves backward-compatibility with everything already shipped in P0.

---

## 6. Impact on the implementation plan

### 6.1 Phase changes

| Phase | Change |
|---|---|
| **P0** | Already shipped. The visual refinement (§5.2 / §5.3 / §5.4) ships as a small follow-up PR — it's a token tweak + density default. |
| **P1a** | Was: build a single dashboard. **Now:** build the role-resolved shell + the **Super Admin** and **Drilling Engineer** dashboards (these two cover the demo's two strongest narratives). Asset Manager and Geologist follow once we have content. |
| **P1b** | Unchanged. Auto-Summary block lands in the Drilling Engineer dashboard automatically once Backend PR-1 merges. |
| **P2** | Unchanged. |
| **P3** | The Drilling Engineer dashboard's "My briefings" + "Lessons applicable now" blocks become live once Backend PR-3 + PR-4 merge. |
| **P4** | Asset Manager weekly-digest action wires to Resend. |
| **P5** | Geologist + QA Steward dashboards ship in polish phase, behind the role switcher. |

### 6.2 New work items

- **W-1:** Visual token refinement PR (§5.2). Small.
- **W-2:** Mono numerals + density-default flip (§5.3, §5.4). Small.
- **W-3:** Dashboard block library scaffolding (§3.1). Medium.
- **W-4:** Role switcher in user menu + first-run chip prompt (§3.2, §3.3). Small.
- **W-5:** Super Admin layout (P1a). Medium.
- **W-6:** Drilling Engineer layout (P1a → P3 fills it in). Medium.
- **W-7:** Asset Manager layout (P4 timing aligns with email digest). Medium.
- **W-8:** Geologist + QA Steward layouts (P5). Medium each.

### 6.3 Backend report updates

The five new asks (B-007 through B-011) get added to [BACKEND_REPORT.md](BACKEND_REPORT.md) §Phase 1a Open Items once this plan is signed off. B-008 is the only one frontend would consider blocking on; the others have graceful degradation paths.

---

## 7. Open questions for you

1. **Roles list — is the v1 set right?** Specifically: do we need **Reservoir Engineer** in v1, or is "Drilling Engineer covers production data via auto-summary" enough? My take: defer.
2. **Default role** — Drilling Engineer for the demo? (That's where the §39 killer features land cleanest.)
3. **Visual refinement scope** — happy with the §5.2 token tweaks, or do you want to go further (e.g., custom domain icons, replace Inter with IBM Plex Sans, full chart-skin guide)? My take: ship §5.2 now, evaluate further moves after we have real screens populated.
4. **B-008 priority** — is the backend team OK adding `GET /entities/distinct` early? It unlocks both the Briefing form *and* the Geologist dashboard.
5. **Role gating** — for demo, every user sees every role's dashboard via the switcher. Confirm we don't need role-based access control in v1.
6. **Scope of "Active well of interest"** — frontend-pinned doc per user, or a backend "favorite" concept? My take: frontend-only for v1.

---

## 8. Recommended decision

If the answers above land in the obvious places:

- **Keep** five roles: super_admin, drilling_engineer, geologist, asset_manager, qa_steward.
- **Build** Super Admin + Drilling Engineer in P1a; Asset Manager in P4; Geologist + QA in P5.
- **Apply** the §5.2 token refinement and §5.3 mono-numerals as a small follow-up PR this week.
- **Add** B-007 and B-008 to the backend ask list (high priority); defer B-009/B-010/B-011.
- **Default role** = Drilling Engineer; first-run picker exposed inline on first dashboard load.

Awaiting review before any of this lands in the implementation plan or the backend report.

---

*End of Roles & Dashboards plan v0.1 — draft for review.*
