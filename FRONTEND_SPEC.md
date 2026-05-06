# WellSynth AI — Frontend Specification (Suite Edition)

> **Version:** 2.0 — *Premium Suite*  
> **Date:** 2026-05-05  
> **Audience:** Frontend developer(s) working in a **separate repository**.  
> **Backend base URL:** configurable via env var `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`)  
> **Scope:** This is the entire WellSynth AI product surface — not just a chat client. It is a **document intelligence suite** for petroleum / well-data workflows: ingest, organize, explore, analyze, compare, share.

---

## 0. Reading Guide

| § | Section | What you'll find |
|---|---------|------------------|
| 1 | North Star & Pillars | What we're building and why |
| 2 | Stack | Recommended (not mandatory) |
| 3 | Brand & Design System | Palette, typography, depth, motion, micro-interactions, voice |
| 4 | Information Architecture | Workspaces → Documents → Pages → Evidence |
| 5 | Routes & Screens | Full route map, per-screen specs |
| 6 | Component Inventory | Every reusable component |
| 7 | API Contract | Every endpoint + payload |
| 8 | Auth & Security | Demo-level scope |
| 9 | Email (Resend) | Frontend-owned |
| 10 | State Management | Zustand + TanStack Query |
| 11 | Real-time & Polling | How "live" updates work |
| 12 | Performance Budgets | |
| 13 | Accessibility | WCAG 2.1 AA |
| 14 | Telemetry | PostHog |
| 15 | Testing Strategy | |
| 16 | Error Handling UX | Every failure mode |
| 17 | Folder Structure | |
| 18 | Environment Variables | |
| 19 | Milestones | |
| 20 | Frontend ↔ Backend wire | |
| 21 | Citation Rendering | |
| 22 | Responsive Breakpoints | |
| 23 | Command Palette & Shortcuts | Cmd+K everywhere |
| 24 | Empty States Catalog | Every "zero" screen |
| 25 | Loading & Skeleton Catalog | Every "wait" state |
| 26 | Notifications Center | In-app inbox |
| 27 | Export Center | PDF / CSV / Markdown / Evidence pack |
| 28 | Annotations & Pinboard | Save evidence, build collections |
| 29 | Multi-Doc Chat & Compare | Cross-document workflows |
| 30 | Domain Browsers | Entities, tables, images, formations |
| 31 | Saved Searches & Bookmarks | |
| 32 | Sharing & Read-Only Links | |
| 33 | Feature Flags | |
| 34 | Onboarding & First-Run | |
| 35 | Brand Voice & Copy Guide | |
| 36 | Print & PDF Views | |
| 37 | Internationalization | Future-ready scaffolding |
| 38 | Definition of Premium | Quality bar |
| **39** | **Demo v1 Killer Features** | **Pre-Spud Briefing · Lessons-in-Context · Auto-Summary · Compare** |
| **40** | **Demo v1 Scope (in / out)** | What ships, what's deferred |

---

## 1. North Star & Product Pillars

WellSynth AI is the **document intelligence cockpit** for subsurface engineers. A user walks in with hundreds of unstructured well reports, and walks out with answers, evidence, and exports.

### Five product pillars

1. **Ingest** — drop PDFs, watch real-time per-page progress, see cost + token usage.
2. **Explore** — every page, table, image, and entity is browsable, searchable, sortable.
3. **Ask** — chat with one document, a project, or the entire workspace. Every answer is cited.
4. **Compare** — diff and align wells side-by-side; surface contradictions.
5. **Share** — export evidence packs, PDF reports, read-only links, email digests.

> **A user should never feel like they're using a tech demo.** Every screen earns its place. Every interaction has a defined empty / loading / error / success state.

---

## 2. Recommended Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Framework | **Next.js 15 (App Router, RSC)** | Best DX, streaming, route-level code splitting |
| Styling | **Tailwind CSS 4 + shadcn/ui + Radix primitives** | Premium defaults, full a11y, no runtime CSS-in-JS |
| Animation | **Framer Motion** + **`view-transitions` API** | Premium feel, page transitions |
| State (client) | **Zustand** | Tiny, no boilerplate |
| State (server) | **TanStack Query v5** | Caching, polling, optimistic updates |
| Forms | **React Hook Form + Zod** | Type-safe, mirrors backend Pydantic |
| Markdown | **react-markdown + remark-gfm + rehype-katex** | Tables, GFM, math support |
| Charts | **Recharts** or **visx** | Job throughput, cost, page-token charts |
| PDF rendering (optional) | **react-pdf** (pdf.js) | If we ever inline PDFs (we currently use page PNGs) |
| Map | **MapLibre GL JS + maptiler-style** | Well-location map (open source, no token burn) |
| Icons | **Lucide** + **Tabler Icons** (fallback) | Consistent stroke, tree-shakeable |
| Email | **Resend** SDK via Next.js API routes | |
| Auth | **iron-session** (demo-grade JWT cookie) | Minimal, sufficient for demo |
| Tables | **TanStack Table v8** | Sort/filter/virtualize |
| Notifications | **Sonner** (toasts) + custom inbox panel | |
| Command palette | **cmdk** | The de-facto Cmd+K library |
| Date | **date-fns** | Tree-shakeable |
| Testing | Vitest + Testing Library + Playwright + axe-core | |
| Error monitoring | **Sentry** | Optional in demo |
| Analytics | **PostHog** (self-host friendly) | Optional in demo |
| Deployment | Vercel **or** Docker container | |

> Stack is a recommendation. Equivalent toolchains delivering the same UX are acceptable.

---

## 3. Brand & Design System

### 3.1 Brand Personality

- **Premium, confident, technical, calm.** Not playful. Not "AI-hype-y."
- Voice: precise, terse, neutral. Like a senior engineer's notebook.
- No emojis in product chrome (allowed in chat content if the model emits them).
- Micro-interactions are subtle — never bouncy.

### 3.2 Color Tokens (Dark, default)

```css
:root {
  /* Surfaces */
  --bg:           #0b0d12;   /* app canvas (deeper than v1 for premium contrast) */
  --bg-elev-0:    #0f1115;   /* primary surface */
  --panel:        #14171d;   /* cards, sidebar, modals */
  --panel-2:      #1a1d24;   /* nested cards, hover */
  --panel-3:      #21252e;   /* popovers, dropdowns */
  --border:       #262a33;   /* hairlines */
  --border-soft:  #1d2028;   /* subtler dividers */

  /* Text */
  --text:         #e6e8ee;
  --text-strong:  #ffffff;
  --muted:        #8a8f99;
  --muted-2:      #5f6470;

  /* Accent (primary brand) */
  --accent:       #5b8cff;
  --accent-hover: #7ba3ff;
  --accent-soft:  rgba(91, 140, 255, 0.12);
  --accent-ring:  rgba(91, 140, 255, 0.40);

  /* Semantic */
  --ok:    #22c55e;   --ok-soft:    rgba(34,197,94,0.12);
  --warn:  #f59e0b;   --warn-soft:  rgba(245,158,11,0.12);
  --bad:   #ef4444;   --bad-soft:   rgba(239,68,68,0.12);
  --info:  #06b6d4;   --info-soft:  rgba(6,182,212,0.12);

  /* Score gradient (for retrieval scores 0..1) */
  --score-low:   #6b7280;
  --score-mid:   #f59e0b;
  --score-high:  #22c55e;

  /* Premium gradients (use sparingly) */
  --grad-hero:   linear-gradient(135deg, #5b8cff 0%, #8e6bff 100%);
  --grad-glow:   radial-gradient(800px 400px at 50% -10%, rgba(91,140,255,0.20), transparent 60%);
  --grad-card:   linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.00));
}
```

### 3.2.1 Color Tokens (Light)

Light is a **first-class** theme. Same semantic tokens, different values. Selected via the `[data-theme="light"]` attribute on `<html>` (or `prefers-color-scheme` when user pref = "system").

```css
[data-theme="light"] {
  /* Surfaces — warm-cool neutrals, not pure white */
  --bg:           #f7f8fa;   /* app canvas */
  --bg-elev-0:    #ffffff;   /* primary surface */
  --panel:        #ffffff;   /* cards, sidebar, modals */
  --panel-2:      #f3f5f8;   /* nested cards, hover */
  --panel-3:      #eef1f5;   /* popovers, dropdowns */
  --border:       #e3e6ec;   /* hairlines */
  --border-soft:  #eef0f4;

  /* Text */
  --text:         #14171d;
  --text-strong:  #000000;
  --muted:        #5f6470;
  --muted-2:      #8a8f99;

  /* Accent — slightly deepened for AA contrast on white */
  --accent:       #2f5fe0;
  --accent-hover: #4774eb;
  --accent-soft:  rgba(47, 95, 224, 0.10);
  --accent-ring:  rgba(47, 95, 224, 0.35);

  /* Semantic — deepened for white-bg contrast */
  --ok:    #15803d;   --ok-soft:    rgba(21,128,61,0.10);
  --warn:  #b45309;   --warn-soft:  rgba(180,83,9,0.10);
  --bad:   #b91c1c;   --bad-soft:   rgba(185,28,28,0.10);
  --info:  #0e7490;   --info-soft:  rgba(14,116,144,0.10);

  /* Score gradient */
  --score-low:   #9ca3af;
  --score-mid:   #b45309;
  --score-high:  #15803d;

  /* Premium gradients (lighter) */
  --grad-hero:   linear-gradient(135deg, #2f5fe0 0%, #6b46d6 100%);
  --grad-glow:   radial-gradient(800px 400px at 50% -10%, rgba(47,95,224,0.10), transparent 60%);
  --grad-card:   linear-gradient(180deg, rgba(0,0,0,0.015), rgba(0,0,0,0.00));
}
```

**Theme selection:**

| Preference | Behaviour |
|---|---|
| `dark` (default) | `<html data-theme="dark">` |
| `light` | `<html data-theme="light">` |
| `system` | Reads `window.matchMedia("(prefers-color-scheme: light)")`, applies tokens, listens for changes |

- Persist user choice in `ws.preferences.theme`.
- Avoid FOUC: inline a tiny pre-hydration script in `<head>` that sets `data-theme` from `localStorage` before React mounts (Next.js `app/layout.tsx`).
- All shadcn/Radix primitives must be configured to read from these tokens (no hard-coded colors).
- **Both themes are AA-tested.** No screen ships unless it passes axe-core in both themes.

### 3.3 Depth & Elevation

Shadow strength differs per theme — softer in light, deeper in dark.

| Level | Use | Dark | Light |
|-------|-----|------|-------|
| 0 | Page | flat | flat |
| 1 | Cards | `0 1px 2px rgba(0,0,0,0.4)` | `0 1px 2px rgba(15,20,30,0.06)` |
| 2 | Hover / nested | level 1 + `0 4px 12px rgba(0,0,0,0.35)` | level 1 + `0 4px 12px rgba(15,20,30,0.06)` |
| 3 | Popovers | `0 12px 32px rgba(0,0,0,0.55)` | `0 12px 32px rgba(15,20,30,0.10)` |
| 4 | Modals | backdrop blur 8 px + `0 20px 60px rgba(0,0,0,0.70)` | backdrop blur 8 px + `0 20px 60px rgba(15,20,30,0.18)` |

### 3.4 Typography

| Role | Font | Size | Weight | Tracking |
|------|------|------|--------|----------|
| Display | **Inter Display** (or Inter) | 32–48 px | 700 | -0.02em |
| H1 | Inter | 28 px | 600 | -0.015em |
| H2 | Inter | 22 px | 600 | -0.01em |
| H3 | Inter | 18 px | 600 | normal |
| Body | Inter | 14–15 px | 400 | normal |
| Body-sm | Inter | 13 px | 400 | normal |
| Label | Inter | 12 px | 500 | 0.04em uppercase (where appropriate) |
| Code / Data | `ui-monospace, JetBrains Mono, SFMono-Regular, Menlo` | 13 px | 400 | normal |
| Numeric tabular | Inter, `font-variant-numeric: tabular-nums` | inherit | inherit | |

Line-height: 1.45 for body, 1.25 for headings.

### 3.5 Spacing, Radius, Borders

- Base unit **4 px**. Spacing scale: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48 / 64.
- Card radius **12 px**, button **8 px**, input **8 px**, pill **999 px**.
- Borders **1 px** `--border`. Avoid > 1 px outside hero cards.
- Sidebar: **288 px** expanded, **64 px** collapsed.

### 3.6 Motion

- Defaults: `cubic-bezier(0.2, 0.8, 0.2, 1)`, **150 ms** for hovers, **220 ms** for panels, **320 ms** for page transitions.
- Reduced-motion: respect `prefers-reduced-motion: reduce` — replace transforms with opacity-only.
- View Transitions API for route changes where supported.
- Chat bubble in: 160 ms fade + 6 px translate-y.
- Skeleton shimmer: 1.6 s linear-gradient sweep.

### 3.7 Iconography

- **Lucide** primary (1.5 px stroke).
- 16 px in dense rows, 20 px in toolbars, 24 px in empty states.
- Always paired with `aria-label` if no visible text.

### 3.8 Imagery & Illustrations

- Empty states use minimal 1-color line illustrations on `--panel` background.
- No stock photos in product chrome.
- Page thumbnails are real PNGs from the backend with a 1 px `--border` and 8 px radius.

### 3.9 Micro-Interactions Catalog

| Interaction | Spec |
|---|---|
| Button press | 70 ms scale 0.98 then bounce back |
| Card hover | translate-y −1 px + shadow level 2 |
| Citation chip click | Pulse + scroll evidence panel target into view (smooth) |
| Drag hover (drop zone) | Border becomes accent, background `--accent-soft`, label fades to "Drop to ingest" |
| Toast | Slide from top-right, 220 ms |
| Copy-to-clipboard | Icon swap to ✓ for 1.2 s, toast confirms |
| Loading shimmer | Skeleton blocks with shimmer; never spinners except for user-initiated short waits |
| Score badge | Color interpolated along `--score-*` gradient |
| Streaming text | Subtle 2-char trailing cursor `▍` if backend streams |

---

## 4. Information Architecture

```
Workspace (single-tenant for demo, multi later)
└── Project (a.k.a. "Field" or "Asset")
    └── Document (a.k.a. "Well Report" — one PDF, one ingestion job)
        ├── Pages[]   (per-page markdown + image + bbox crops)
        ├── Tables[]
        ├── Entities[]   (depths, formations, fluids, dates, units)
        └── Images[]
└── Sessions (chat conversations, scoped to workspace/project/document)
└── Saved Searches
└── Pinboard (saved evidence cards)
└── Reports (composed exports)
```

- A user can scope chat to: **all workspace docs / one project / one document / a pinboard collection**.
- A document belongs to one project. Default project: "Inbox."
- Projects are pure UI — for v1 they map to a frontend-only tag set persisted in `localStorage` + Resend-emailed reports. (Backend has no project concept yet — flag as future.)

---

## 5. Application Routes & Screens

### 5.1 Route Map

| Route | Screen | Purpose |
|-------|--------|---------|
| `/` | **Home / Dashboard** | KPIs, recent activity, quick actions |
| `/onboarding` | **First-run wizard** | Sample doc, guided tour |
| `/upload` | **Ingest Studio** | Drop, queue, monitor jobs |
| `/projects` | **Projects** | List of projects (frontend-tagged) |
| `/projects/:id` | **Project detail** | Documents in this project |
| `/documents` | **Document Library** | Grid/list of all documents |
| `/documents/:jobId` | **Document Workspace** | Tabbed: Overview / Pages / Tables / Entities / Images / Activity |
| `/documents/:jobId/pages/:n` | **Page Detail** | Image + extracted markdown side-by-side |
| `/documents/:jobId/compare/:otherJobId` | **Compare** | Two-doc diff view |
| `/chat` | **Chat** | Single & multi-doc conversation |
| `/chat/:sessionId` | **Chat (resumed)** | |
| `/search` | **Search** | Semantic + filters + saved searches |
| `/entities` | **Entity Browser** | All depths/formations/etc across workspace |
| `/tables` | **Table Browser** | All extracted tables, filterable |
| `/images` | **Image Browser** | All extracted images by type |
| `/map` | **Map View** | Wells plotted on a basemap (when coords exist) |
| `/pinboard` | **Pinboard** | Saved evidence cards |
| `/reports` | **Reports** | Composed exports (PDF / Markdown / CSV bundles) |
| `/notifications` | **Notifications** | In-app inbox |
| `/activity` | **Activity Feed** | Timeline of ingestions, chats, exports |
| `/settings` | **Settings** | Profile, API key, preferences, theme |
| `/settings/keys` | **API Keys & Secrets** | Demo-only |
| `/help` | **Help & Shortcuts** | Cheat sheet, links |
| `/login` | **Login** | Demo auth |
| `/share/:token` | **Read-only Share** | Public, sandboxed view of one chat or report |

### 5.2 Dashboard (`/`)

**Hero strip**
- Greeting ("Good evening, Sriharsha"), workspace name, last activity
- Three primary CTAs: **Upload PDF**, **Ask a question**, **Open last document**

**KPI cards** (use tabular-nums)
- Documents ingested · Pages processed · Tables extracted · Entities resolved · Avg ingestion time · Token spend (USD)

**Active jobs** — live polling cards (see §5.4).

**Recent activity** — timeline (ingestions, chats, exports, pins).

**Suggested questions** — for each recently ingested doc, show 3 AI-suggested starter prompts (precomputed from document summary; see §29.3).

**Onboarding nudges** — dismissible cards: "Try the Map view," "Pin your first evidence card."

### 5.3 Onboarding (`/onboarding`)

3 steps, skippable:
1. **Welcome** — what WellSynth does (45 sec read).
2. **Sample data** — option to load a bundled sample PDF (`GSWA_Harvey_1_WCR.pdf` already in `data/output`) so the user has something to chat with immediately.
3. **Tour** — coachmarks (powered by [`driver.js`](https://driverjs.com)) over the dashboard, chat, and document workspace.

### 5.4 Ingest Studio (`/upload`)

**Top bar:** "Drag PDFs anywhere on this page, or click to choose." Max 20 files / 500 MB each.

**Active queue** (left, 60% width)
- Each file = `JobCard`:
  - filename, size, upload progress, then ingestion progress
  - phase chip (`extraction`, `validation`, `escalation`, `postprocessing`, `indexing`)
  - per-page progress: `14 / 22 pages` + ring + ETA (computed from rate)
  - cancel button (calls future cancel endpoint; for now disabled with tooltip)
  - on completion: ✓ green + actions: "Open document," "Start chat," "Pin"
- Cards reorder so active jobs sit above completed.

**History** (right, 40%)
- Recent completed/failed jobs, click → document workspace.

**Cost sidebar** (collapsible) — running USD spend across jobs (use pricing config from backend manifest).

### 5.5 Document Workspace (`/documents/:jobId`)

Tabbed shell. Header carries: filename, project (editable chip), status badge, page count, ingestion date, "Chat about this document" CTA, "Export" menu.

**Tabs**

1. **Overview**
   - Auto-summary card (first 800 chars of `full_document.md`, or a future endpoint that returns a model-generated summary)
   - Key entities chips (top 8 entities by count): depths, formations, dates
   - Top tables (3 cards)
   - Top images (3 cards)
2. **Pages** — left rail of thumbnails (lazy via `GET /ingest/{job_id}/pages/{n}/image`), main pane shows selected page image + markdown side-by-side with a draggable splitter.
3. **Tables** — list of all tables (`page_NNN_table_MM`), each rendered from `*.md` (download `.json` available). Sort/filter/full-screen.
4. **Entities** — grouped by type (depth, formation, fluid, date). Each row: canonical value + unit + page badge → click to jump.
5. **Images** — grid by image_type. Click → modal with bbox crop + parent page.
6. **Activity** — ingestion run log (timestamps, retries, escalations, model used per page, token counts).

### 5.6 Page Detail (`/documents/:jobId/pages/:n`)

- Top: page navigation (← / →), zoom controls, "Open in new tab," "Copy page link," "Pin this page."
- Body: split view (image | markdown). Toggle "Show entity highlights" overlays bbox rectangles on the image (when bbox metadata present in entities).
- Bottom drawer (collapsed by default): tables on this page, entities on this page.

### 5.7 Chat (`/chat`)

**Three-pane on ≥1440 px:**

| Left (Sessions) | Center (Conversation) | Right (Evidence) |
|---|---|---|
| Session list, grouped by today/yesterday/older | Message list, composer | Evidence stack: text snippets, table cards, image crops, page thumbnails |

**Composer** — multi-line input, paste-to-send detection, attach scope chip (workspace / project / document / pinboard / compare:A vs B), suggested prompts row above composer when conversation is empty.

**Message rendering**
- Markdown answer with inline `[S1]`–`[Sn]` citations as clickable chips.
- Confidence bar under each message: derived from `claims[].confidence` distribution.
- Status banners:
  - `partial` — yellow strip lists `unanswered[]` facets, with a "Ask the missing piece" button that pre-fills the composer.
  - `no_evidence` — neutral strip; answer disclaimed.
  - `clarification_requested` — composer focuses with the model's clarification question highlighted.
- Per-message actions: copy, copy-with-citations, share link, pin to pinboard, regenerate, "Why this answer?" (opens `plan` + `assessment` debug drawer).
- Streaming UX: even though backend returns full response, render the answer with a typewriter effect (toggleable) for premium feel.

**Right panel — Evidence**
- Cards in the same order as `[Sn]`. Hover-sync: hovering a citation in the answer highlights the matching card; hovering a card highlights the citation.
- Each card has: kind icon, score bar, `doc_name • page N`, snippet/table/image, "Open page" link, "Pin" button.
- Image evidence: lazy-loaded crop, click for full-page modal.
- Table evidence: rendered markdown table, "Download CSV/JSON" buttons (frontend converts the markdown to CSV; JSON via raw table URL).

**Multi-doc & Compare** — see §29.

### 5.8 Search (`/search`)

- Big search input, Enter or live (debounced 300 ms).
- Filters as pill row: workspace / project / document / page / score threshold / kind (text / table / entity / image).
- Result types tabbed: **All / Text / Tables / Entities / Images.**
- Each result card: snippet, doc/page, score bar, "Open page," "Pin," "Use as chat seed."
- "Save search" button → persists to Saved Searches with name + filters.
- "Search history" panel (last 20 queries).

### 5.9 Entity Browser (`/entities`)

- Faceted browser across all entities in the workspace.
- Group by: type (depth/formation/fluid/date) → unit → canonical value.
- Click row → list of pages that mention it.
- Export CSV.

### 5.10 Table Browser (`/tables`)

- Table rows: doc · page · table_id · column count · row count · preview (first 3 cells).
- Click → full table modal with sort/filter and CSV/JSON export.

### 5.11 Image Browser (`/images`)

- Grid by `image_type` (log_plot, cross_section, map, photo, schematic, …).
- Click → modal with description + parent page.

### 5.12 Map View (`/map`)

- MapLibre basemap (dark style).
- Wells plotted when entities `latitude` + `longitude` exist (gracefully empty otherwise — empty state copy: "Coordinates not yet extracted from any document").
- Click marker → mini-card with doc name + "Open document" + "Chat about this well."

### 5.13 Pinboard (`/pinboard`)

- Drag-to-reorder cards (saved evidence).
- Group into "Boards" (e.g., "TD across field," "All lithology calls").
- "Chat with this board" → multi-doc chat scoped to pinned items only.
- Export → PDF report or markdown bundle.

### 5.14 Reports (`/reports`)

- Compose a report: select pinboards + sessions + tables → generates a single PDF / Markdown / .zip evidence pack.
- Saved reports list with re-export.
- "Email report" → Resend.

### 5.15 Notifications (`/notifications`)

- In-app inbox: ingestion completed, ingestion failed, share opened, weekly digest.
- Mark all as read, filter by type.
- Mirrors a bell icon in the top bar with unread count.

### 5.16 Activity (`/activity`)

- Timeline of every action (frontend-tracked + backend-derivable).
- Filter by user, type, date.

### 5.17 Settings (`/settings`)

Sub-tabs:
- **Profile** — name, avatar, signature for emailed reports.
- **Preferences** — density (Comfortable / Compact), **theme (System / Light / Dark)**, motion (Full / Reduced), default chat scope, auto-pin model-suggested follow-ups, typewriter on/off.
- **API & Keys** — base URL, demo JWT, Resend key (masked), Sentry DSN, PostHog key.
- **Notifications** — which events trigger inbox/email.
- **Workspace** — name, branding logo (frontend-only), accent color override (within token-safe range).
- **Danger zone** — clear local data, sign out.

### 5.18 Help (`/help`)

- Keyboard shortcuts cheat sheet.
- Links to docs, changelog, status page.
- "Send feedback" → opens email modal (Resend).

### 5.19 Share (`/share/:token`)

- Public, no-auth view.
- Renders one chat session OR one report read-only.
- Watermark: "Shared via WellSynth AI."
- Token issued from frontend (signed, expires in N days). No backend changes required.

---

## 6. Component Inventory

### Layout & Navigation
- `AppShell` — sidebar + topbar + content slot + right-pane slot
- `Sidebar`, `SidebarItem`, `SidebarGroup`
- `TopBar` — breadcrumb, global search, notifications bell, user menu
- `CommandPalette` (`cmdk`) — global Cmd+K
- `Breadcrumbs` — workspace > project > doc > page
- `Tabs`, `TabBar`
- `SplitPane` — draggable splitter (image | markdown)

### Chat
- `ChatPane`, `MessageList`, `MessageBubble`, `Composer`, `ScopeChip`, `EvidencePanel`, `EvidenceCard` (variants: text/table/image/page), `CitationChip`, `ConfidenceBar`, `StatusBanner`, `SuggestionChip`, `WhyThisAnswerDrawer`

### Ingestion
- `DropZone`, `JobCard`, `JobList`, `PhaseChip`, `ProgressRing`, `CostMeter`

### Documents
- `DocGrid`, `DocCard`, `DocHeader`, `PageThumbnail`, `PageViewer` (zoom/pan), `MarkdownRenderer`, `TableRenderer`, `EntityChip`, `ImageCard`, `ActivityLog`

### Browsers
- `EntityBrowser`, `TableBrowser`, `ImageBrowser`, `FacetSidebar`, `FilterPill`

### Search
- `SearchBar`, `SearchFilters`, `SearchResultCard`, `SavedSearchList`

### Pinboard & Reports
- `Pinboard`, `PinCard`, `BoardSwitcher`, `ReportComposer`, `ReportPreview`

### Map
- `WellMap`, `WellMarker`, `WellPopover`

### Shared UI (mostly shadcn/ui)
- `Button` (primary / secondary / ghost / destructive / link)
- `IconButton`
- `Badge` (status, score, kind)
- `Chip` / `Pill`
- `Skeleton` (text, card, image, row)
- `Modal` / `Dialog` / `Drawer`
- `Toast` (Sonner)
- `EmptyState`
- `KeyboardKey` (renders `⌘K`)
- `Tooltip`
- `Popover`
- `DropdownMenu`
- `ContextMenu`
- `ScoreBar`
- `Card`, `CardHeader`, `CardBody`, `CardFooter`
- `KPIStat`
- `ConfirmDialog`
- `CopyButton`
- `DownloadButton`
- `ShareDialog`
- `Avatar`
- `UserMenu`
- `NotificationBell`
- `ActivityRow`
- `TimeAgo`

---

## 7. API Contract

### 7.1 Chat — `POST /chat`

**Request:**
```json
{
  "message": "What is the total depth of Harvey-1?",
  "session_id": "uuid-optional",
  "job_id": "uuid-optional",
  "doc_name": "optional-string"
}
```

**Response:**
```json
{
  "session_id": "uuid",
  "status": "answered | partial | no_evidence | clarification_requested",
  "answer": "Markdown string with [S1] [S2] citations",
  "claims": [
    { "text": "claim text", "source_id": "S1", "confidence": "high | medium | low" }
  ],
  "unanswered": [
    { "facet": "facet description", "reason": "why" }
  ],
  "followup": "Suggested follow-up question or null",
  "sources": [
    {
      "source_id": "S1",
      "text": "chunk text",
      "score": 0.92,
      "doc_name": "GSWA_Harvey_1_WCR",
      "page_num": 4,
      "chunk_index": 2,
      "kind": "text | table | entity | image",
      "table_id": "page_010_table_01",
      "entity_type": "depth",
      "canonical_value": "3200 m",
      "unit": "m",
      "image_type": "log_plot",
      "description": "image description",
      "collection": "wellsynthai_documents | wellsynthai_entities | wellsynthai_images"
    }
  ],
  "evidence": [
    {
      "kind": "table | bbox_crop | page_image",
      "source_id": "S1",
      "doc_name": "...",
      "page_num": 10,
      "url": "/ingest/{job_id}/pages/10/image/crop?ymin=200&xmin=50&ymax=600&xmax=950",
      "table_id": "page_010_table_01",
      "table_markdown": "| Col1 | Col2 |\n|---|---|",
      "image_type": "log_plot",
      "description": "...",
      "bbox": [200, 50, 600, 950]
    }
  ],
  "attached_pages": [
    { "job_id": "...", "page_num": 4, "url": "/ingest/.../pages/4/image" }
  ],
  "tool_calls_made": 3,
  "plan": { "resolved_question": "...", "facets": [], "strategy": "..." },
  "assessment": { "coverage": "full|partial|none", "quality": "high|medium|low" }
}
```

### 7.2 Ingestion

```
POST /ingest/upload    multipart/form-data — field "files": File[]
POST /ingest           JSON — { "file_paths": string[] }
GET  /ingest                                   list
GET  /ingest/{job_id}                          status / manifest
GET  /ingest/{job_id}/full                     full document markdown (text/plain)
GET  /ingest/{job_id}/pages/{n}/image          page PNG
GET  /ingest/{job_id}/pages/{n}/image/crop     ?ymin=&xmin=&ymax=&xmax=  (0..1000)
```

**Job manifest:**
```json
{
  "job_id": "uuid",
  "filename": "original.pdf",
  "status": "pending | processing | completed | failed",
  "total_pages": 22,
  "pages_processed": 14,
  "current_phase": "extraction | validation | escalation | postprocessing | indexing",
  "output_dir": "data/output/...",
  "doc_name": "...",
  "error": "string if failed",
  "tokens": { "flash_in": 0, "flash_out": 0, "pro_in": 0, "pro_out": 0, "embed": 0 },
  "cost_usd": 0.0,
  "started_at": "ISO-8601",
  "completed_at": "ISO-8601 | null"
}
```

### 7.3 Search — `GET /search`

```
?q=...&limit=10&job_id=...&doc_name=...&page_num=...&score_threshold=0.5
```

```json
{
  "query": "...",
  "count": 10,
  "results": [
    {
      "id": "point-id",
      "score": 0.89,
      "payload": {
        "text": "chunk content",
        "doc_name": "...",
        "page_num": 4,
        "chunk_index": 2,
        "job_id": "..."
      }
    }
  ]
}
```

### 7.4 Static Assets

```ts
const pageImageUrl = (jobId: string, page: number) =>
  `${API_URL}/ingest/${jobId}/pages/${page}/image`;

const cropUrl = (jobId: string, page: number, [ymin, xmin, ymax, xmax]: number[]) =>
  `${API_URL}/ingest/${jobId}/pages/${page}/image/crop?ymin=${ymin}&xmin=${xmin}&ymax=${ymax}&xmax=${xmax}`;
```

### 7.5 Frontend-Only API Routes (Next.js)

| Route | Purpose |
|---|---|
| `POST /api/auth/login` | Demo JWT cookie issue |
| `POST /api/auth/logout` | Clear cookie |
| `GET  /api/auth/me` | Whoami |
| `POST /api/email/share` | Resend — share a chat or report |
| `POST /api/email/export` | Resend — emailed transcript |
| `POST /api/email/digest` | Resend — weekly digest |
| `POST /api/share/issue` | Issue a signed share token |
| `GET  /api/share/resolve?token=` | Decode token to a chat/report payload |
| `POST /api/feedback` | Resend → ops@ |

### 7.6 Persistence Surfaces (Frontend-only for v1)

These live in `localStorage` (or IndexedDB for larger payloads) under namespaced keys:

- `ws.projects` — projects list (id, name, color, doc job_ids)
- `ws.pinboard` — boards + cards
- `ws.reports` — composed reports
- `ws.savedSearches`
- `ws.bookmarks`
- `ws.notifications`
- `ws.activity`
- `ws.preferences`

Backend awareness of these is a **future** concern. Flag in code with `// FUTURE: persist to backend`.

---

## 8. Auth & Security (Demo Level)

| Concern | Implementation |
|---|---|
| Identity | Single demo user, env-configured `DEMO_EMAIL` / `DEMO_PASSWORD` |
| Session | iron-session JWT in httpOnly + SameSite=Strict + Secure cookie |
| Token TTL | 24 h, no refresh |
| API protection | Frontend attaches `Authorization: Bearer <jwt>` to backend calls (backend currently ignores it — future-proof) |
| Backend guard | None for demo; do not bake assumptions of "trusted browser only" |
| CORS | Backend allows configured frontend origin(s) |
| File upload | Client-side: reject non-PDF, enforce 500 MB cap before POST |
| Share tokens | HMAC-signed JWT (separate secret), TTL 7 days, scope = chat session id OR report id; revocable via frontend list |
| Secrets in UI | Mask all key fields; provide reveal toggle only on user action |

**Future (prod):** OAuth 2.0 / SAML via Azure AD or Auth0; backend middleware that validates the JWT and binds requests to a workspace. **Not in scope for v1.**

---

## 9. Email (Resend)

Templates (React Email recommended):
- **share** — "X shared a chat with you" + read-only link
- **export** — chat transcript as styled HTML + .md attachment
- **digest** — weekly summary: docs ingested, top questions, exports
- **feedback-receipt** — confirmation to user
- **failure-alert** — sent when an ingestion fails (opt-in, configurable in Settings)

All via Next.js API routes; **never expose `RESEND_API_KEY` to the browser**.

---

## 10. State Management

```
┌─ Zustand (client state) ─────────────────────────────────┐
│ auth         user, jwt, demo flag                         │
│ ui           sidebarOpen, density, theme, motion, palette │
│ chat         activeSessionId, sessionsMap, scope          │
│ ingestion    activeJobsMap, costRunning                   │
│ pinboard     boards, cards                                │
│ projects     list, activeProjectId                        │
│ notifications inbox, unreadCount                          │
│ shortcuts    paletteOpen, lastUsed                        │
└──────────────────────────────────────────────────────────┘

┌─ TanStack Query (server cache) ──────────────────────────┐
│ ["jobs"]                  GET /ingest                     │
│ ["job", id]               GET /ingest/{id}  (poll 2s)     │
│ ["job", id, "full"]       GET /ingest/{id}/full           │
│ ["search", params]        GET /search                     │
│ ["chat", sessionId]       optimistic, no GET endpoint     │
│ ["page", jobId, n]        derived (for prefetch)          │
└──────────────────────────────────────────────────────────┘
```

- Optimistic chat: append user message instantly; bot bubble shows skeleton + animated dots until response.
- Job polling: `refetchInterval: status==="processing" ? 2000 : false`.
- Library: `staleTime: 60_000`, refetch on focus.
- Prefetch on hover for any link to a heavy route (Document Workspace, Page Detail).

---

## 11. Real-time & Polling

Backend has no WebSocket. We use:
- **Polling** (TanStack Query) for ingestion job manifests.
- **EventSource (SSE) — future** for live chat token streaming. Until then, render full responses with optional typewriter effect.
- **Visibility-aware polling** — pause `refetchInterval` when the tab is hidden (TanStack does this by default).

---

## 12. Performance Budgets

| Metric | Target |
|---|---|
| LCP (dashboard) | < 1.2 s |
| INP | < 200 ms (p75) |
| CLS | < 0.05 |
| JS initial route | < 150 KB gz |
| Chat composer keystroke → render | < 16 ms |
| Page thumbnail batch first-paint | < 800 ms for 20 thumbs |
| Document grid | virtualize > 50 items |
| Image lazy load | IntersectionObserver, `loading="lazy"`, low-res placeholder |
| Streaming render | first token visible < 100 ms after fetch resolves |
| Memory ceiling per tab | < 350 MB on a 100-doc workspace |

Build-time:
- Route-level code splitting (App Router default).
- Dynamic-import the Map view, MarkdownRenderer, TableExplorer, ReportComposer, CommandPalette.
- Tree-shake icons (`import { X } from "lucide-react"` only).

---

## 13. Accessibility

- WCAG 2.1 AA on every screen.
- All shadcn/Radix primitives keep focus management correct — preserve their props.
- Keyboard:
  - Cmd+K command palette
  - `/` focuses global search
  - `g d` jump to dashboard, `g c` chat, `g u` upload, `g s` search (Vim-style nav, optional, behind `keyboardNav` flag)
  - `Esc` closes any modal/drawer
  - Arrow keys navigate page thumbnails
  - `c` opens composer focus
  - `?` shows shortcuts cheat sheet
- Screen-reader: chat list `role="log"` + `aria-live="polite"` for new bot messages; never auto-announce while typing.
- Focus rings always visible (use `--accent-ring`).
- Color contrast checked via axe-core in CI.
- Reduced motion respected globally.

---

## 14. Telemetry

PostHog (self-host friendly). No PII in events.

| Event | Properties |
|---|---|
| `app.loaded` | route, theme, density |
| `upload.started` | file_count, total_bytes |
| `upload.completed` | job_id, pages, duration_ms |
| `upload.failed` | job_id, error_class |
| `chat.sent` | scope, message_length, has_attachment |
| `chat.answered` | session_id, status, sources_count, evidence_count, ms |
| `chat.regenerated` | session_id |
| `chat.cited_clicked` | source_kind |
| `evidence.pinned` | kind |
| `pinboard.created` | |
| `report.exported` | format, count |
| `search.performed` | filters, has_threshold |
| `share.issued` | scope_type |
| `command_palette.opened` | trigger |
| `error.boundary` | component, message_hash |

Funnels of interest: Upload → Chat → Export.

---

## 15. Testing Strategy

| Layer | Tool | Coverage |
|---|---|---|
| Unit | Vitest + Testing Library | 80% of components |
| Integration | Vitest + MSW | All API call shapes (use the JSON contracts in §7 as fixtures) |
| E2E happy paths | Playwright | upload → chat → cite → pin → export |
| Visual regression | Playwright screenshots | dashboard, chat, doc workspace, page detail, pinboard |
| Accessibility | axe-core in Playwright | zero serious/critical violations on every route |
| Perf budgets | Lighthouse CI | enforce budgets in §12 on PR |

---

## 16. Error Handling UX

| Scenario | UI |
|---|---|
| API unreachable | Top banner: "Cannot reach server" + retry; routes still render skeletons |
| 401 from backend | Toast + redirect to `/login` |
| 413 upload too large | Inline error + suggested action (split PDF) |
| Unsupported file type | Inline error: "Only PDF accepted." |
| Ingestion failure | JobCard turns red; details modal exposes `error` and links to "Retry" (re-dispatches via `POST /ingest`) |
| Chat 500 | Bubble: "Something went wrong." Retry button repeats the same prompt. |
| `no_evidence` | Soft banner; show whatever the model returned with a disclaimer |
| `partial` | Yellow strip listing facets + composer pre-fill action |
| `clarification_requested` | Composer focuses with the model's question highlighted |
| Share token expired | `/share/:token` shows "Link expired — request a new one" |
| Local storage exceeded | Toast + automatic eviction of oldest activity entries |
| Sentry-caught crash | ErrorBoundary screen with friendly copy + "Reload" + "Send report" |

---

## 17. Folder Structure (Recommended)

```
wellsynth-frontend/
├── app/
│   ├── (auth)/
│   │   └── login/page.tsx
│   ├── (main)/
│   │   ├── layout.tsx                     ← AppShell
│   │   ├── page.tsx                       ← Dashboard
│   │   ├── onboarding/page.tsx
│   │   ├── upload/page.tsx
│   │   ├── projects/
│   │   │   ├── page.tsx
│   │   │   └── [id]/page.tsx
│   │   ├── documents/
│   │   │   ├── page.tsx
│   │   │   └── [jobId]/
│   │   │       ├── page.tsx
│   │   │       ├── pages/[n]/page.tsx
│   │   │       └── compare/[otherJobId]/page.tsx
│   │   ├── chat/
│   │   │   ├── page.tsx
│   │   │   └── [sessionId]/page.tsx
│   │   ├── search/page.tsx
│   │   ├── entities/page.tsx
│   │   ├── tables/page.tsx
│   │   ├── images/page.tsx
│   │   ├── map/page.tsx
│   │   ├── pinboard/page.tsx
│   │   ├── reports/page.tsx
│   │   ├── notifications/page.tsx
│   │   ├── activity/page.tsx
│   │   ├── settings/
│   │   │   ├── page.tsx
│   │   │   ├── keys/page.tsx
│   │   │   └── preferences/page.tsx
│   │   └── help/page.tsx
│   ├── share/[token]/page.tsx
│   └── api/
│       ├── auth/
│       │   ├── login/route.ts
│       │   ├── logout/route.ts
│       │   └── me/route.ts
│       ├── email/
│       │   ├── share/route.ts
│       │   ├── export/route.ts
│       │   └── digest/route.ts
│       ├── share/
│       │   ├── issue/route.ts
│       │   └── resolve/route.ts
│       └── feedback/route.ts
├── components/
│   ├── chat/
│   ├── documents/
│   ├── ingestion/
│   ├── browsers/                          ← entity, table, image
│   ├── search/
│   ├── pinboard/
│   ├── reports/
│   ├── map/
│   ├── layout/
│   └── ui/                                ← shadcn primitives + atoms
├── lib/
│   ├── api.ts
│   ├── store/                             ← zustand slices
│   ├── queries/                           ← tanstack hooks
│   ├── markdown/                          ← citation parser, renderer
│   ├── share/                             ← token sign/verify
│   ├── shortcuts.ts
│   ├── telemetry.ts
│   ├── feature-flags.ts
│   ├── utils.ts
│   └── types.ts                           ← mirrors backend response shapes
├── public/
│   └── illustrations/
├── styles/
│   └── globals.css
├── emails/                                ← React Email templates
│   ├── share.tsx
│   ├── export.tsx
│   └── digest.tsx
├── tests/
│   ├── unit/
│   └── e2e/
├── .env.local
├── tailwind.config.ts
├── next.config.ts
└── package.json
```

---

## 18. Environment Variables

```env
# Required
NEXT_PUBLIC_API_URL=http://localhost:8000

# Auth (demo)
AUTH_SECRET=<random-32-char>
DEMO_EMAIL=demo@wellsynth.ai
DEMO_PASSWORD=demo123

# Share tokens
SHARE_TOKEN_SECRET=<random-32-char>

# Email
RESEND_API_KEY=re_...
EMAIL_FROM=noreply@wellsynth.ai

# Optional
SENTRY_DSN=https://...
NEXT_PUBLIC_POSTHOG_KEY=phc_...
NEXT_PUBLIC_POSTHOG_HOST=https://us.posthog.com
NEXT_PUBLIC_FEATURE_FLAGS=map=true,vimNav=false
```

---

## 19. Milestones

| Phase | Scope | Demoable in |
|---|---|---|
| **M1 — Core Loop** | Dashboard, Upload, Chat (single-doc), Evidence panel, Document Workspace (Overview + Pages tabs), basic Settings | Sprint 1 |
| **M2 — Library & Browsers** | Document Library, Tables tab, Entities tab, Images tab, Search | Sprint 2 |
| **M3 — Pinboard, Reports, Multi-Doc** | Pinboard, Reports, Multi-doc / Compare chat, Saved Searches | Sprint 3 |
| **M4 — Premium Polish** | Command palette, Notifications, Activity, Onboarding, Map view (gracefully empty), Empty/Loading catalogs | Sprint 4 |
| **M5 — Sharing & Email** | Share tokens, Resend templates, Reports email | Sprint 5 |
| **M6 — Hardening** | Sentry, PostHog, axe-core CI, Lighthouse CI, perf pass, error boundaries, prod build | Sprint 6 |

---

## 20. Frontend ↔ Backend Wire

```
┌────────────────────┐   HTTPS / fetch   ┌─────────────────────────┐
│ Next.js (browser)  │ ────────────────▶ │ FastAPI (uvicorn :8000) │
│                    │ ◀──────────────── │                         │
└────────────────────┘    JSON / PNG     └─────────────────────────┘
        │                                          │
        │  POST /chat                              │ Celery worker(s)
        │  POST /ingest, POST /ingest/upload       │  ↳ ingest tasks
        │  GET  /ingest/{id}        (poll 2 s)     │
        │  GET  /search                            │
        │  GET  /ingest/{id}/pages/{n}/image       │
        │  GET  /ingest/{id}/pages/{n}/image/crop  │
        │  GET  /ingest/{id}/full                  ▼
        │                                  ┌─────────────────┐
        │                                  │ Redis + Qdrant  │
        │                                  └─────────────────┘
        ▼
┌────────────────────┐
│ Next.js API routes │
│  /api/auth/*       │  iron-session JWT cookie
│  /api/email/*      │  Resend SDK (server-only)
│  /api/share/*      │  HMAC token issue/resolve
│  /api/feedback     │  Resend SDK
└────────────────────┘
```

**Truths:**
- All document data lives behind FastAPI. Frontend never touches Redis/Qdrant directly.
- Page images and crops are served by the backend; frontend uses `<img src>` with the URLs above.
- Backend has no auth middleware in v1; the frontend is the gate. The app sends `Authorization: Bearer <jwt>` so the backend can ignore it now and validate later without breaking the wire.
- Backend has no email and no share concept — both are 100% frontend-owned for v1.
- Backend has no project/pinboard/report concept — frontend persists these locally and a future migration moves them server-side.

---

## 21. Citation Rendering Logic

The chat `answer` contains markers `[S1]`, `[S2]`, … which map to entries in `sources[]` by `source_id`.

Pipeline:
1. After response arrives, build a map `{ S1: source, S2: source, ... }`.
2. Pre-process answer markdown: replace `[Sn]` with a placeholder span the renderer turns into a `<CitationChip>`.
3. Each chip:
   - Visible: small superscript pill with `n`, colored by score band.
   - Hover: tooltip with first 120 chars of source text.
   - Click: smooth-scroll the matching `EvidenceCard` into view + pulse.
   - Right-click: context menu (Copy link, Open page, Pin).
4. If `evidence[].source_id == Sn`, the card prefers the visual variant (image crop or table) over text.
5. "Copy with citations" → emits markdown like:
   ```
   The total depth was 3,200 m [^S1].

   [^S1]: GSWA_Harvey_1_WCR, page 4 — "TD reached at 3,200 m..."
   ```

---

## 22. Responsive Breakpoints

| Width | Layout |
|---|---|
| ≥ 1600 px | 3-pane chat, full sidebar, evidence sticky |
| 1280–1599 px | 3-pane chat, sidebar collapses to icons on demand |
| 1024–1279 px | 2-pane: evidence becomes a right drawer (toggle) |
| 768–1023 px | Single column; sidebar = sheet; evidence = bottom sheet |
| < 768 px | Mobile: stacked, bottom-tab nav (Dashboard / Chat / Library / More) |

---

## 23. Command Palette & Keyboard Shortcuts

Cmd+K (Ctrl+K on Windows) opens the palette. Sections:
- **Go to** — every route in §5.1
- **Recent** — last documents, last chats
- **Actions** — "Upload PDFs", "New chat", "New report", "Pin selected", "Toggle theme", "Sign out"
- **Search** — passes through to `GET /search` if query is > 2 chars
- **Chat** — "Ask: <query>" creates a new session prefilled with the query

| Key | Action |
|---|---|
| `Cmd/Ctrl K` | Command palette |
| `/` | Focus global search |
| `c` | New chat |
| `u` | Upload |
| `g d` | Dashboard |
| `g c` | Chat |
| `g l` | Library |
| `g s` | Search |
| `g p` | Pinboard |
| `g r` | Reports |
| `?` | Shortcuts cheat sheet |
| `Esc` | Close any overlay |
| `[` / `]` | Prev / next page in Page Detail |
| `Enter` | Send composer |
| `Shift Enter` | Newline in composer |
| `Cmd/Ctrl Enter` | Send + start a new chat |

---

## 24. Empty States Catalog

Every "zero" screen has: 1-color line illustration · headline · supporting line · primary CTA · optional secondary link.

| Screen | Headline | CTA |
|---|---|---|
| Dashboard, no docs | "Your workspace is empty." | "Upload your first PDF" |
| Library | "Nothing here yet." | "Go to Ingest Studio" |
| Chat, no session | "Ask anything about your documents." | "New chat" |
| Search, no query | "Search across every page, table, and entity." | "Try: 'total depth Harvey-1'" |
| Search, no results | "No matches at that threshold." | "Lower the score threshold" |
| Pinboard | "Nothing pinned yet." | "Pin from any chat or page" |
| Reports | "No reports composed." | "Compose a report" |
| Notifications | "All caught up." | — |
| Map | "No coordinates extracted." | "Open the latest document" |
| Activity | "No activity yet." | — |
| Entity Browser, type | "No <type> entities resolved." | "Open entity tab on a document" |

---

## 25. Loading & Skeleton Catalog

Rule: never a generic spinner where structure can be inferred. Always **content-shaped skeletons**.

| Surface | Skeleton |
|---|---|
| KPI cards | 4 rectangle blocks with shimmer |
| Job card | filename row + progress row + phase chip placeholders |
| Document grid | 12 card skeletons, 16:10 thumbnail block + 2 lines |
| Page thumbnails rail | column of 16:11 blocks |
| Page Detail | image block + 14 markdown lines (mixed widths) |
| Chat bubble (bot) | 3 lines + animated 3-dot indicator |
| Evidence card | line, score bar, 2 snippet lines |
| Search results | 6 row skeletons with score-bar placeholder |
| Map | dimmed canvas + center spinner (only acceptable spinner) |

---

## 26. Notifications Center

In-app inbox + bell with unread count.

| Type | Trigger | Action |
|---|---|---|
| `ingest.completed` | poller sees `status==="completed"` | "Open document" |
| `ingest.failed` | `status==="failed"` | "View error" |
| `share.opened` | `/api/share/resolve` hit | "View chat" |
| `report.ready` | report export finishes | "Download" |
| `digest.weekly` | scheduled (frontend `setInterval` while open + email via Resend) | — |

User can mute types in Settings → Notifications.

---

## 27. Export Center

Outputs:

| Format | Source | Notes |
|---|---|---|
| **PDF** | Chat session, Pinboard, Report | Headless render via `@react-pdf/renderer` server-side |
| **Markdown** | Chat session, Document, Report | Includes citations as footnotes |
| **CSV** | Tables, Search results, Entities | Tabular exports |
| **JSON** | Tables (`*.json` raw), Sources | Machine-readable |
| **Evidence pack (.zip)** | Pinboard or Report | Bundles MD + cited page PNGs + crops + tables |
| **Email** | Same payloads | Via Resend, with attachment fall-back to "open in browser" link |

UI: every exportable surface gets a `DownloadButton` dropdown with format choices.

---

## 28. Annotations & Pinboard

- **Pin** any chat answer, evidence card, page, table, or entity → goes to a board.
- Boards: rename, recolor, reorder, share, export.
- Annotation shape (frontend, in localStorage):
  ```ts
  type Pin = {
    id: string;
    boardId: string;
    kind: "answer" | "evidence" | "page" | "table" | "entity" | "search";
    payload: any;        // typed per kind
    note?: string;       // user note
    tags?: string[];
    createdAt: string;
  };
  ```
- "Chat with this board" creates a chat session with scope = the union of `(job_id, doc_name, page_num)` of the pins. The current `/chat` API doesn't accept arrays of scopes; in v1 we send the **most-pinned doc** as `doc_name` and prepend the prompt with a system instruction listing the pinned page numbers. Flag as future for backend support of arrays.

---

## 29. Multi-Doc Chat & Compare

### 29.1 Multi-doc chat
- Scope chip in the composer: "Workspace" or "All in project X" or a multi-select of documents.
- For workspace/project scope, omit `job_id` and `doc_name` from the request — backend already searches across the workspace.

### 29.2 Compare (`/documents/:jobA/compare/:jobB`)
- Two columns, identical layout: page thumbnails + selected page + entities.
- Entity diff: highlight depths/formations present in one but not the other.
- Built-in chat: "Compare TD across these wells." Sends a single chat request with no scope and prefixes the message with: `Compare {docA} and {docB}: <user message>`.

### 29.3 Suggested Questions
- Generated client-side from the document's top entities + table titles. Examples:
  - For each formation entity → "What is the depth range of <formation>?"
  - For each table → "Summarize <table title>."
  - Domain templates: TD, KOP, formation tops, mud system, casing program, perforation intervals.
- Render up to 5 chips in the empty chat state and on the document Overview tab.

---

## 30. Domain Browsers

### 30.1 Entity Browser facets
- type ∈ {depth, formation, fluid, date, latitude/longitude, casing_size, mud_weight, …}
- unit ∈ {m, ft, psi, ppg, °C, …}
- canonical_value range slider (numeric where applicable)
- doc / project filter
- click-through to source page

### 30.2 Table Browser
- Sort by row count, column count, page, doc
- Free-text filter on header rows
- Modal preview with TanStack Table (sort/filter inside the table itself)
- "Send to chat" button → seeds a prompt: "Summarize this table:" + table markdown

### 30.3 Image Browser
- Filter by `image_type`
- Lightbox modal: image + description + parent page link
- "Cite this image" → adds to current chat as an attached evidence reference

---

## 31. Saved Searches & Bookmarks

- Saved Search: name + query + filters → one-click re-run.
- Bookmark: any URL inside the app (page, chat, table). Lives in sidebar drawer.
- Bookmarks support tags, drag-reorder.

---

## 32. Sharing & Read-Only Links

- Issue from chat or report: "Share" → choose TTL (24 h / 7 d / 30 d) → copy link.
- Link format: `https://<frontend>/share/<token>`.
- Token = signed JWT (`SHARE_TOKEN_SECRET`), payload `{kind: "chat"|"report", id, exp}`.
- The share page is server-rendered; it fetches the underlying chat/report, strips PII fields, and renders a static read-only view.
- Revocation list in `localStorage` → token resolution endpoint checks it and returns `expired`.

---

## 33. Feature Flags

- Read from `NEXT_PUBLIC_FEATURE_FLAGS` env (`key=value,key2=value2`).
- Optional runtime override via PostHog feature flags.
- Suggested flags for v1: `map`, `vimNav`, `typewriter`, `compare`, `multiDocChat`, `audioInput` (off).

---

## 34. Onboarding & First-Run

- First load → `/onboarding` if `localStorage.ws.preferences.onboardedAt` is missing.
- Step 1: Welcome card, 3 bullets, "Show me around" / "Skip."
- Step 2: "Load sample document" — copies the bundled `Harvey_1` job into the user's workspace view.
- Step 3: Tour with `driver.js` — 5 stops: sidebar, command palette, upload, chat, evidence panel.
- Persistent "Re-take tour" button in `/help`.

---

## 35. Brand Voice & Copy Guide

- **Tone:** precise, terse, neutral, confident.
- Avoid: marketing fluff, exclamation marks, "magical," "awesome," "wow."
- Use sentence case for buttons and labels except product-name nouns.
- Numbers: tabular nums, comma thousand separators, units always rendered (e.g., `3,200 m` not `3200`).
- Time: relative under 7 days (`2 hours ago`), absolute beyond (`12 Apr 2026`).
- Errors: state the problem + the next action. ("Cannot reach server. Retry.") never the stack trace.
- Empty states are an opportunity to teach — short, concrete, action-first.

---

## 36. Print & PDF Views

- `@media print` styles for: chat session, page detail, report.
- Print-only header: WellSynth AI · doc name · date · page number.
- All citations expand to footnotes when printing.
- PDF generation via `@react-pdf/renderer` mirrors the print layout (server-rendered for fidelity).

---

## 37. Internationalization

- v1 ships English only.
- Wrap all user-visible strings in `t("key")` from `next-intl` from day one.
- ICU MessageFormat for plurals (`"{count, plural, =0 {no pages} one {1 page} other {# pages}}"`).
- Numeric/date formatting via `Intl.NumberFormat` / `Intl.DateTimeFormat`.

---

## 38. Definition of Premium

Every screen passes this checklist before merging:

- [ ] Has a designed empty state, loading skeleton, error state, success state.
- [ ] Keyboard-navigable end-to-end. Visible focus rings.
- [ ] Reduced-motion compliant.
- [ ] Contrast ratio ≥ 4.5:1 in **both** light and dark themes.
- [ ] Renders correctly in System / Light / Dark; no hard-coded colors.
- [ ] No layout shift on data load.
- [ ] All numeric values use tabular-nums.
- [ ] All async actions show optimistic feedback < 100 ms.
- [ ] No emoji in chrome.
- [ ] Copy reviewed against §35 voice guide.
- [ ] Lighthouse: Performance ≥ 90, A11y ≥ 95.
- [ ] Has at least one delightful micro-interaction (per §3.9 catalog).
- [ ] Works at 360 px width without horizontal scroll.
- [ ] Renders cleanly when offline (cached) — at minimum, an offline banner.

---

*End of Frontend Specification — Suite Edition v2.0.*

---

## 39. Demo v1 Killer Features

These four features are the demo's center of gravity. They turn WellSynth from "another chat-with-PDFs app" into something a drilling engineer would defend in front of their boss. All four are backed by **real, new backend endpoints** documented in `BACKEND_SPEC.md`.

> **Implementation rule:** every output below is a **structured response with citations**, not a markdown blob. The frontend renders structured data into premium components — never just dumps text.

### 39.1 Pre-Spud Risk Briefing

**Why it matters.** Before spudding a new well, an engineer reads 10–20 offset reports to find what went wrong nearby. This takes 2–3 engineer-days and is the single most-asked report in any drilling team. We compress it to ~30 seconds.

**The flow.**

```
/briefings  ─────────► list of past briefings
   │
   └─► /briefings/new ──► structured form (planned well attributes)
                  │
                  └─► POST /briefings/pre-spud
                          │
                          └─► /briefings/:id ──► report view (premium printable layout)
```

#### Screen `/briefings/new`

Form (React Hook Form + Zod):

- **Planned well name** (text)
- **Field / Asset** (text, autocomplete from ingested doc metadata)
- **Planned section** (chip select: surface / intermediate / production / completion)
- **Planned formations** (multi-chip; suggestions seeded from the entity collection)
- **Planned MD range** (two number inputs: from–to, units toggle ft/m)
- **Planned TVD range** (optional)
- **Operation focus** (multi-select: drilling / casing / cementing / logging / completion)
- **Scope corpus** (chips: workspace / project / specific docs)

CTA: **"Generate briefing"** → posts to `POST /briefings/pre-spud` → optimistic skeleton on `/briefings/:id` while awaiting response.

#### Screen `/briefings/:id`

Premium report layout — designed to be exported as PDF and pinned to a Teams channel.

```
┌──────────────────────────────────────────────────────────────┐
│ Pre-Spud Risk Briefing                          [Export PDF] │
│ ALPHA-12  ·  Eagle Ford  ·  Production section                │
│ Generated 5 May 2026  ·  Based on 14 offset wells              │
├──────────────────────────────────────────────────────────────┤
│  Risk Summary                                                 │
│  ┌──────────────┬──────────────┬──────────────┐              │
│  │ 5 NPT risks  │ 3 hazards    │ 14 offsets   │              │
│  └──────────────┴──────────────┴──────────────┘              │
├──────────────────────────────────────────────────────────────┤
│  Top NPT Risks (ranked by frequency × duration)              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ 1. Stuck pipe in Eagle Ford  ░░░░░░░░░░  6 / 14 wells   │  │
│  │    Avg duration: 38 h  ·  IADC: 5.1                     │  │
│  │    Root cause (when recorded): differential sticking    │  │
│  │    What worked: spotting pill, reduced overbalance      │  │
│  │    Evidence: 4 citations  [page cards inline]           │  │
│  └────────────────────────────────────────────────────────┘  │
│  … 4 more risk cards …                                        │
├──────────────────────────────────────────────────────────────┤
│  Formation Hazards                                            │
│  • Lost circulation in Austin Chalk @ 2,800 m   3 / 14 wells │
│  • Overpressure window 2,950 – 3,100 m TVD       2 / 14      │
├──────────────────────────────────────────────────────────────┤
│  Mitigations Library  (cited)                                 │
│  • Pre-spot LCM pill before Austin Chalk … [S1][S2]          │
│  • Mud weight ramp 9.6 → 10.4 ppg before 2,950 m … [S3]      │
├──────────────────────────────────────────────────────────────┤
│  Offset Wells Considered (14)                                 │
│  table: well · field · TVD · NPT total · last spud date       │
│  → click row to open document workspace                       │
└──────────────────────────────────────────────────────────────┘
```

**Components:**
- `BriefingHeader` — title, scope chips, generation timestamp, export menu
- `RiskCard` — frequency bar, duration stat, IADC chip, root-cause line, "what worked" line, citations row
- `HazardRow` — formation name, hazard type, depth band, frequency, citations
- `MitigationItem` — bullet text + citation chips
- `OffsetWellTable` — TanStack Table sortable

**Empty state.** "No offsets ingested for this field yet — upload some DDRs and try again."

**Premium polish.**
- Print stylesheet renders `/briefings/:id` cleanly on A4 with header, page numbers, footer.
- "Email briefing" → Resend, with PDF attachment.
- "Pin briefing" → goes to Pinboard.
- Side rail shows the source citations panel (same component as chat) so the user can drill into any number quoted in the report.

---

### 39.2 Lessons-in-Context

**Why it matters.** Every operator has a lessons-learned database. Almost nobody reads it — they don't know to query it at the right moment. We surface lessons **proactively** based on the user's current operational context.

**The flow.** Two surfaces:

1. **Standalone browser** at `/lessons` — search & filter all lessons across the workspace.
2. **Contextual widget** — appears in the right rail of any Document Workspace, of `/briefings/:id`, and of any chat session that has a clear operational context. Auto-fetches the top 3 applicable lessons.

#### Screen `/lessons`

```
┌────────────────────────────────────────────────────────────┐
│ Lessons     [filter: formation ▾] [op type ▾] [section ▾]  │
├────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────────────────┐   │
│ │ Stuck pipe avoidance — Eagle Ford 9⅝" running         │   │
│ │ Situation: differential sticking observed at 3,180 m   │   │
│ │ Outcome:   28 h NPT, recovered with spotting pill      │   │
│ │ Fix:       reduce overbalance to 0.3 ppg before 3.1 km │   │
│ │ From: ALPHA-09 EOWR · page 18  [open]                  │   │
│ └──────────────────────────────────────────────────────┘   │
│ … more lesson cards …                                       │
└────────────────────────────────────────────────────────────┘
```

#### Widget `<LessonsInContext>`

A right-rail card that takes a `context` prop:

```ts
type Context = {
  formation?: string;
  section?: "surface" | "intermediate" | "production" | "completion";
  depth_md_m?: number;
  operation?: "drilling" | "casing" | "cementing" | "logging" | "completion";
  doc_name?: string;   // implicit context
  job_id?: string;
};
```

It calls `POST /lessons/applicable` and renders 3–5 ranked lesson cards. Each card carries:
- Title
- One-line situation, outcome, fix
- Provenance chip (doc + page + score)
- Actions: **Open page**, **Pin**, **Use in chat** (seeds composer with "What if I apply lesson X here?")

**Premium polish.**
- Subtle accent border on the widget when a *high-relevance* lesson lands (score ≥ 0.85) — drawing the eye without screaming.
- The widget collapses to a single line ("3 lessons applicable") if the user dismisses it; reappears on context change.
- Telemetry event `lessons.surfaced` + `lessons.engaged` to measure adoption (the only thing that matters for lessons systems).

---

### 39.3 Document Auto-Summary + Key Facts

**Why it matters.** When an engineer opens a document, they want a **30-second read** of what's inside, not 80 pages of markdown. Today we don't even render a summary — they have to scroll the full document.

**The flow.** Backend produces this once per document at the end of ingestion (one extra Gemini call) and persists it to the manifest. Frontend just renders.

#### Where it shows up

1. **Library card** (`/documents`)
   - Doc card grows by ~40 px to show 3 key facts: TD · formations count · NPT total hours.
   - Tooltip on each fact reveals the citation.
2. **Document Workspace → Overview tab**
   - **`KeyFactsPanel`** — six facts in a 2×3 grid:
     - Total Depth (MD / TVD)
     - Spud date / Completion date
     - Formations penetrated (count + chip list of names)
     - Casing program (sizes summary, e.g., "13⅜ → 9⅝ → 7")
     - Mud system (one-line description)
     - NPT total (hours + IADC top categories)
   - **`AutoSummaryCard`** — 3-paragraph summary (~250 words). Markdown rendering. "Open in chat" button seeds a session with the doc.
3. **Dashboard hero strip** — shows last-ingested doc's key facts as a "what just landed" widget.
4. **Search results** — auto-summary first 240 chars used as the snippet (better than a random chunk).

**Components:**
- `KeyFactsPanel`
- `KeyFact` — label, value, unit, optional citation chip
- `AutoSummaryCard` — markdown body + "Read full document" link

**Premium polish.**
- Numeric values use tabular nums + the user's preferred units (settings).
- Each fact carries a citation badge → click → scrolls Page Detail to that page.
- If a fact failed to extract (low confidence or missing), render a soft "—" with tooltip "Not confidently extracted."

---

### 39.4 Cross-Document Compare

**Why it matters.** "How does ALPHA-12 compare to ALPHA-09?" is a daily question. Today the engineer has two browser tabs and their eyes. We give them a structured diff.

**Spec already drafted in §5 (Compare view).** This section adds the **structured-API-backed** version.

#### Screen `/documents/:jobA/compare/:jobB`

Three regions, equal width on ≥1440 px:

| Left column (Doc A) | Center diff | Right column (Doc B) |
|---|---|---|
| Doc A header + key facts | Side-by-side comparable rows with delta chips | Doc B header + key facts |

The center column is the headline:

```
┌──────────────────────────────────────────────────┐
│ Comparable                A          B    Δ      │
│ ─────────────────────────────────────────────────│
│ Total depth (MD)       3,380 m   3,420 m  +40 m  │
│ Spud date              12 Mar    03 Apr   +22 d  │
│ Formations penetrated  9         11       +2     │
│ Common formations      8 / both              ✓    │
│ Only in A              Austin Chalk only     ⚠    │
│ Only in B              Wolfcamp, Bone Spring     │
│ NPT total              42 h      78 h    +36 h    │
│ Top NPT cause A        Stuck pipe                 │
│ Top NPT cause B        Lost circulation           │
│ Casing program         13⅜→9⅝→7   13⅜→9⅝→7  match │
│ Mud system             OBM 9.6   WBM 9.4    diff  │
└──────────────────────────────────────────────────┘
```

Below: a **"Plot together"** strip — formation tops as horizontal bars on a shared TVD axis.

CTA: **"Ask about this comparison"** → opens chat session with both job_ids in scope and a system prefix like *"Comparing ALPHA-12 vs ALPHA-09. Answer using both."*

**Components:**
- `CompareHeader` — two doc badges + "swap" arrows + scope chip
- `CompareRow` — label, value-A, value-B, delta chip (color: ok / warn / bad)
- `CompareCallout` — single-row highlight for important deltas (e.g., NPT > 30 h delta)
- `FormationStripChart` — Recharts horizontal bars
- `ChatAboutComparison` button → seeds a chat

**Premium polish.**
- Sticky compare header — scrolls with the user.
- Color the delta chip green if "doc B improves on A," amber if drift, red for risk-elevated deltas (NPT increase, mud-weight increase >0.5 ppg).
- Print stylesheet so the compare view is a clean PDF.

---

### 39.5 Where each feature plugs into the existing app

| Feature | Add to existing screens |
|---|---|
| Pre-Spud Briefing | New top-level route `/briefings`. Add "New briefing" CTA on Dashboard hero strip. |
| Lessons-in-Context | New `/lessons` route + widget injected into Document Workspace right rail and `/briefings/:id` right rail. Also a quick action in the Command Palette. |
| Auto-Summary + Key Facts | Extend Document Library cards, Dashboard hero, Document Workspace Overview tab, Search snippets. |
| Compare | The compare screen already specced; just makes it real by calling `POST /compare` instead of computing client-side. |

### 39.6 New API endpoints (frontend's view — full spec in `BACKEND_SPEC.md`)

```
POST /briefings/pre-spud         → returns BriefingResult (structured)
GET  /briefings                  → list briefings (history)
GET  /briefings/:id              → single briefing

POST /lessons/applicable         → ranked Lesson[] given context

GET  /documents/:jobId/summary   → { summary, key_facts, citations }
                                   (read-only; produced at ingestion-time)

POST /compare                    → { rows[], deltas[], common_entities, only_in_a[], only_in_b[] }
```

All four return JSON with the same `provenance` shape used by `/chat` so the citation components are reused everywhere.

### 39.7 New component additions to §6 inventory

- `BriefingForm`, `BriefingHeader`, `RiskCard`, `HazardRow`, `MitigationItem`, `OffsetWellTable`
- `LessonsInContext` (widget), `LessonCard`, `LessonFilterBar`
- `KeyFactsPanel`, `KeyFact`, `AutoSummaryCard`
- `CompareHeader`, `CompareRow`, `CompareCallout`, `FormationStripChart`, `ChatAboutComparison`

### 39.8 Telemetry additions

| Event | Properties |
|---|---|
| `briefing.created` | corpus_size, formations_count, ms |
| `briefing.exported` | format |
| `lessons.surfaced` | context_kind, top_score |
| `lessons.engaged` | action ∈ {open_page, pin, use_in_chat} |
| `summary.viewed` | doc_id |
| `compare.opened` | job_a, job_b |
| `compare.shared` | format |

---

## 40. Demo v1 Scope — What Ships, What's Deferred

The full §5 route map describes the *target* product. Demo v1 ships a strict subset because the backend cannot honor the rest yet.

### 40.1 In scope for demo v1 (built or buildable on current backend)

- Dashboard
- Onboarding
- Upload / Ingest Studio
- Document Library
- Document Workspace (tabs: Overview · Pages · Tables · Entities · Images · Activity)
- Page Detail
- Chat (single-doc, multi-doc, scope chip)
- Search
- Entity Browser · Table Browser · Image Browser
- Pinboard
- Reports / Export Center
- Map (gracefully empty)
- Notifications
- Activity feed
- Settings (Profile · Preferences · API & Keys · Notifications · Workspace · Danger zone)
- Help
- Read-only Share (`/share/:token`)
- **§39 features:** Briefings · Lessons · Auto-Summary · Compare

### 40.2 Deferred (require backend work not yet started — flag clearly in the UI as "coming soon" or omit entirely)

| Screen | Why deferred |
|---|---|
| Projects (`/projects/*`) | No backend project model; can ship as **frontend-only tags** persisted to localStorage if PM wants it. Current ruling: **omit from demo v1.** |
| Wells / Sections / Campaign hierarchy | No L3 backend model |
| Artifact revisions UI | No L4 schema |
| Action Board (`/actions`) | No actions table |
| Risks browser | No risks table |
| Drafts review queue | No drafts state machine |
| Audit log viewer | No audit table |
| Approvals workflow | No approver concept |
| Daily Summary / Shift Handover | Require L3 + actions; deferred |
| Offset Intelligence (graph traversal) | Requires Neo4j + L2 entity resolution. **Pre-Spud Briefing is the 80/20 stand-in for demo.** |

### 40.3 Behavior for deferred features

- Do **not** stub them with localStorage and pretend they work.
- Do **not** add menu items that lead to "coming soon" pages — that's demo theatre.
- The single exception: **Settings → "Preview features"** has a clearly-labeled toggle that surfaces the future-roadmap routes as disabled menu items with tooltips. Off by default.

### 40.4 Updated milestone alignment

The §19 milestones now read:

| Phase | Scope |
|---|---|
| **M1 — Core Loop** | Dashboard · Upload · Chat · Document Workspace (Overview + Pages) · Settings |
| **M2 — Library & Browsers** | Library · Tables · Entities · Images · Search |
| **M3 — Killer Features** | **Auto-Summary · Pre-Spud Briefing · Lessons-in-Context · Compare** |
| **M4 — Pinboard & Reports** | Pinboard · Report composer · Export center · Email |
| **M5 — Polish** | Command palette · Notifications · Activity · Onboarding · Map (empty) · Empty/Loading catalogs · A11y pass |
| **M6 — Hardening** | Sentry · PostHog · Lighthouse CI · axe-core CI · prod build |

M3 is the demo headline.

*End of v2.0 + §39/§40.*
