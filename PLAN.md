# WellSynth AI — Master Implementation Plan (v4)

> **v4 supersedes v3.** v3 (`PLAN_v3_ingestion.md`, archived) covered only the document
> ingestion pipeline. v4 reframes the project against the **WellSynth SRS/PRD Functional
> Spec v3** (`WellSynth_Final_SRS_PRD_Functional_Specification_v3.pdf`) and turns the
> ingestion pipeline into one module of a larger Well Operations Intelligence product.
>
> **North-star goals (in priority order)**
> 1. **≥ 99% extraction accuracy** on dense well-report content (depths, NPT events,
>    formation tops, mud weights, table values, BHA components).
> 2. **Source-grounded citations on every meaningful output** — every answer/summary
>    references the artifact + page + bbox region it came from.
> 3. **Image grounding** — when an answer relates to a figure, plot, mud log strip, or
>    well schematic, return the cropped page region image alongside the text.
> 4. **Human-supervised** — the system drafts; humans approve. Never autonomous decisions.
> 5. **Cross-well comparability** — structured entities (NPT, formations, depths, fluids)
>    are normalized to a controlled vocabulary so wells, sections, and campaigns can be
>    compared and aggregated.

---

## 0. PRD → Product Mapping

The PRD describes **WellSynth** as a role-aware Well Operations Intelligence workspace
covering planning, live operations, handover, campaign memory, and decision support. This
plan implements it as **eight backend capability layers** that map to PRD sections:

| # | Capability layer | PRD sections | What it delivers | State |
|---|---|---|---|---|
| L1 | Document Intelligence (ingestion) | §10, §18 | Per-page MD + tables + image descriptions + entities, with provenance | **Built (v3)** |
| L2 | Domain Knowledge Layer (entities + taxonomy) | §4, §11 | Normalized well/section/NPT/formation/fluid entities; controlled vocab | Not started |
| L3 | Tenant & Hierarchy Model | §3, §4.2, §9 | operator → asset → field → campaign → rig → well → section objects | Not started |
| L4 | Artifact Linkage & Versioning | §10, §11 | Artifact class taxonomy, mandatory metadata, revisions, scope linkage | Not started |
| L5 | Retrieval Backbone (RAG + KG) | §13.2 (Search/Offset/Lessons), §6 | Hybrid Qdrant + graph; source-cited answers; image grounding | Not started |
| L6 | Operational Synthesis Agents | §13.2 (Summary/Handover/Program QA/Risks) | Daily summary, handover, program QA, action board, lessons retrieval | Not started |
| L7 | RBAC & Governance | §7, §19 | Scope-based access, approval routes, audit, AI guardrails | Not started |
| L8 | API + Workspace UI | §12, §13.1, FastAPI | Endpoints + minimal workspace UI for pilot | API stub only |

The current codebase implements **L1 only**. The rest of this plan defines the path to
the PRD-MVP scope (PRD §21) with accuracy and grounding as the gating criteria.

---

## 1. Architecture (v4)

```
                     ┌─────────────────────────────────────────┐
                     │  Workspace UI / API (L8)                 │
                     │  Role-aware tabs: Overview, Program,     │
                     │  Offsets, Timeline, Handover, Risks,     │
                     │  Documents, Lessons                      │
                     └──────────────┬───────────────────────────┘
                                    │ scoped by L7 RBAC
        ┌───────────────────────────┼───────────────────────────┐
        ▼                           ▼                           ▼
┌────────────────┐        ┌──────────────────┐        ┌──────────────────┐
│ Synthesis      │        │ Retrieval        │        │ Workspace State  │
│ Agents (L6)    │◀──────▶│ Backbone (L5)    │◀──────▶│ (L3 + L4)        │
│ Summary,       │        │ Hybrid RAG +     │        │ Hierarchy,       │
│ Handover,      │        │ KG, image        │        │ artifacts,       │
│ Program QA,    │        │ grounding,       │        │ actions, risks,  │
│ Lessons        │        │ source citation  │        │ lessons          │
└────────┬───────┘        └────────┬─────────┘        └────────┬─────────┘
         │                         │                           │
         └─────────────┬───────────┴───────────────────────────┘
                       ▼
              ┌─────────────────────────┐
              │ Domain Knowledge (L2)   │
              │ Controlled vocab, NPT   │
              │ codes, depth/unit norm  │
              │ Entity resolution       │
              └────────────┬────────────┘
                           │
                           ▼
              ┌─────────────────────────────────────────────┐
              │ Document Intelligence — Ingestion (L1)      │
              │ Per-page rasterize → Flash/Pro → MD +       │
              │ tables + image descriptions + entities +    │
              │ bboxes + page PNGs persisted for grounding  │
              └─────────────────────────────────────────────┘
```

The accuracy backbone is **L1 → L2**: extraction quality and entity normalization
determine everything downstream. L5–L6 cannot exceed the accuracy of what L1 captured.

---

## 2. The Accuracy Strategy (the path to ≥ 99%)

99% on every numeric/entity field is the gating goal. Single-pass extraction will not
hit it. The strategy is layered defenses, each closing a class of errors the layer above
cannot catch.

| Layer | Defense | Target error class |
|---|---|---|
| A1 | Per-page Flash extract + Flash validate (current v3) | Generic vision/OCR errors |
| A2 | Pro escalation on iter-3 (current v3) | Hard pages Flash cannot handle twice |
| A3 | **Adaptive DPI** + **ROI second-look** on validator failure | Small print, dense logs, missed table rows |
| A4 | **Schema-constrained extraction** — `responseSchema` with enums for NPT codes, depth types, units | Free-text drift, unit ambiguity |
| A5 | **Numeric guardrails** — every depth/pressure/weight value checked against PyMuPDF baseline + unit-range sanity check | Hallucinated numbers, decimal-point errors |
| A6 | **Cross-page reconciliation** — formation tops, depth sequences, and table column shapes validated for monotonicity/consistency | Page-isolation blind spots |
| A7 | **Entity resolution** vs canonical store — wells, formations, rigs deduped across docs | Naming drift; false-new entities |
| A8 | **SME review queue** for low-confidence or high-severity entities (NPT > 12h, casing depth changes) | Residual model error |
| A9 | **Golden-set CI** — 50–100 hand-labeled pages from real well reports, regression-tested every prompt or model change | Silent drift over time |

Layers A1–A2 are built. **A3–A9 are the heart of the next phase of work** and are sized
into Phase 1A below.

---

## 3. The Grounding & Citation Strategy

Every meaningful output must answer: "where did this come from?" Two levels:

### 3.1 Text grounding
Each extracted entity, table cell, and answer fragment carries a **provenance record**:

```json
{
  "doc_id": "well_ALP-12_DDR_2026-04-23.pdf",
  "page_num": 13,
  "bbox": [120, 340, 480, 410],         // pixel coords on rendered page (gemini returns these)
  "extraction_iter": 1,
  "model": "gemini-3-flash-preview",
  "confidence": 0.92,
  "rendered_page_uri": "data/pages/.../page_013.png"
}
```

The Retrieval Backbone (L5) attaches the provenance set to every answer. The UI shows
**evidence cards** (PRD §13.2 FR-010) clickable down to the exact page region.

### 3.2 Image grounding (the user-requested capability)
When a query is about a figure/plot/log strip/schematic, the system returns the **cropped
page region** alongside the text answer. Implementation:

1. **At ingestion (L1):** Gemini already returns image descriptions per page. v4 extends
   the extractor schema to also return **normalized bounding boxes** for each described
   image (`[ymin, xmin, ymax, xmax]` × 1000, the format `gemini-2.5` natively supports).
2. **Persist:** the rendered page PNG is kept in `data/pages/{doc_id}/page_{n}.png`
   (currently discarded after extraction — v4 adds persistence).
3. **Index:** image descriptions are embedded and indexed in a separate Qdrant collection
   with payload `{doc_id, page_num, bbox, description, type}`.
4. **At query time:** if retrieval hits an image-type chunk OR the user explicitly asks
   for "the diagram/log/schematic of X", L5 crops the bbox from the stored page PNG and
   returns it inline with the answer.

This satisfies the user's image-priority requirement without storing duplicate pixels —
we keep the page PNG once and crop on demand.

### 3.3 Document priority for grounding
The PRD §18.2 evidence hierarchy maps to a **scoring weight** at retrieval time:

| Tier | Artifact class | Weight |
|---|---|---|
| 1 (highest) | Approved drilling program, signed final reports, approved lessons | 1.0 |
| 2 | DDRs, handover notes (released), risk/assurance memos | 0.85 |
| 3 | Specialist artifacts (mud, DD, MWD), service memos | 0.75 |
| 4 | Draft programs, draft notes, unapproved lessons | 0.5 |
| 5 | Ad-hoc media/screenshots, untagged uploads | 0.3 |

The retrieval rerank step multiplies semantic similarity by tier weight. Conflicting
evidence is **surfaced**, not silently resolved (PRD §18.3).

---

## 4. Module-by-Module Implementation

### L1 — Document Intelligence (BUILT)

Implemented per `PLAN_v3_ingestion.md`. Per-page PNG rasterize → Flash extract → Flash
validate → optional Pro escalation. 72/72 tests passing. Outputs per-page MD, table JSON,
full document MD.

**Gaps to close before L2 work** (these are A3/A4 from the accuracy strategy, plus the
groundwork for grounding):

- **G-L1.1** — Persist rendered page PNGs to `data/pages/{doc_id}/page_{n}.png` instead
  of discarding. Required for image grounding (§3.2).
- **G-L1.2** — Extend extractor `responseSchema` to demand bounding boxes for every
  table and every described image. Validator already sees the image, so it can verify.
- **G-L1.3** — Add `entities` field to extractor output (well IDs, depths with unit+type,
  formation tops, NPT events with IADC code, fluids, equipment, dates). Use Gemini's
  `responseSchema` enum constraints sourced from L2 controlled vocab.
- **G-L1.4** — Adaptive DPI: pages flagged `is_scanned` or with high text density
  (chars/area) render at 300 DPI; sparse pages at 150. Cuts cost ~40% with no quality
  loss on easy pages.
- **G-L1.5** — ROI second-look. When validator returns specific gaps (e.g., "rows 5–8
  missing from table at top of page"), iter-2 crops the bbox region (PyMuPDF
  `page.get_pixmap(clip=...)` at 300 DPI) and sends only the crop. Smaller input,
  higher resolution on failure region, better iter-2 success without escalating to Pro.
- **G-L1.6** — Numeric guardrail pass: every `depth`, `pressure`, `weight`, `volume`
  value in entities is checked against (a) presence in PyMuPDF baseline text and
  (b) sane physical range for the unit. Discrepancies → escalate that page to Pro
  even if validator passed.
- **G-L1.7** — **Durable, resumable ingestion pipeline** (no data loss, scales to
  100s of PDFs × 100-700 pages). See §4.L1.1 for the full dossier.

These are the **Phase 1A** scope and are the prerequisites for any RAG/UI work.

---

#### §4.L1.1 — Durable, Resumable Ingestion (Celery + Redis) — Design Dossier

> **Status:** Not built. This subsection is the full architectural plan to be consulted
> when the durability work is picked up. The current in-process pipeline is adequate for
> development but **cannot survive process restarts, reloads, or crashes mid-document**
> — the 23-page Harvey run demonstrated that pages_png/ survived but every other
> artifact (per-page MD, tables, entities, full document) was lost when the process was
> killed before Phase 3 completed.
>
> **Scale target:** hundreds of PDFs concurrently, each 100–700 pages. At 700 pages × 2
> Gemini calls × ~3s = ~70 minutes of LLM time per worst-case doc. Any architecture that
> holds that much state in memory or in a single process is guaranteed to lose work.

##### L1.1.a Failure-mode recap (what we're hardening against)

From the 23-page Harvey 1 retrospective:

| # | Observed failure mode | Cause | Lost |
|---|---|---|---|
| F1 | `pages_png/` present, everything else missing | Uvicorn `--reload` killed the background task mid-Phase-2 | Per-page MD, tables, entities, full_doc |
| F2 | Any exception in Phase 3 flips job to `FAILED` and writes nothing | Big-bang batch persistence — all writes deferred to end | Whole doc, even pages that extracted fine |
| F3 | Job disappears on process restart | `_jobs` is a module-level in-memory dict | Job history, status, error traces |
| F4 | FastAPI `BackgroundTasks` has no supervision | Designed for 1–2s post-response work, not 70-minute jobs | Retry semantics, visibility, guaranteed delivery |
| F5 | Worker dict `job.page_results` grows unbounded | Every page's full state (minus png_bytes) kept in memory | Memory pressure at scale |
| F6 | No way to resume from a partial run | No manifest records which pages have been extracted | Forces full re-rasterize + full re-extract |

The durability architecture must close all six.

##### L1.1.b Principles (non-negotiable)

1. **Per-page durability.** Each page's markdown, tables, entities, and trace are
   persisted **immediately inside the worker** as soon as the LangGraph completes for
   that page. Nothing is held in RAM waiting for a "finalize" step.
2. **Aggregator, not batch.** Phase 3 becomes a pure aggregator that reads per-page
   files from disk and produces `full_document.md` + consolidated `entities.json`.
   Failure in Phase 3 is recoverable by re-running the aggregator — the source data is
   already on disk.
3. **Exactly-once per page, idempotent re-runs.** Re-running a page produces the same
   file contents byte-for-byte (same prompt, temperature=0 or seeded). Re-ingestion
   short-circuits on `page_NNN.extraction_done` marker.
4. **Job state lives outside the web process.** A durable store (Redis for live state,
   SQLite/Postgres for history) is the source of truth. Any web worker, any Celery
   worker, any admin CLI sees the same view.
5. **Explicit failure, never silent.** Every exception is logged with full stack,
   persisted to the job's manifest, and surfaced via the `/ingest/{job_id}` endpoint.
   A `FAILED` job carries `{stage, page_num, error_class, traceback, partial_artifacts}`.
6. **Resumable from any phase boundary.** On worker restart, every in-flight job is
   inspectable, and the user (or an auto-recovery job) can resume from the last
   completed phase.
7. **Horizontally scalable by design.** Adding a Celery worker node = more throughput.
   No shared mutable state between workers.

##### L1.1.c Target architecture

```
                         ┌──────────────────────────────────────┐
                         │  FastAPI (web tier, stateless)        │
                         │  POST /ingest → enqueue Celery task   │
                         │  GET  /ingest/{job_id} → read status  │
                         │    from Redis/SQLite (no in-memory)   │
                         └──────────────┬────────────────────────┘
                                        │ enqueue
                                        ▼
        ┌───────────────────────────────────────────────────────┐
        │  Redis                                                │
        │  ├─ Celery broker (task queue, ack-after-success)     │
        │  ├─ Celery result backend (task status, returns)       │
        │  └─ Job live state: HSET jobs:{id} {phase, pages_done, │
        │     pages_total, last_error, started_at, ...}         │
        └───────────┬───────────────────────────────────────────┘
                    │ workers pull
                    ▼
        ┌───────────────────────────────────────────────────────┐
        │  Celery worker pool (separate processes)              │
        │                                                       │
        │  Canvas per document (Celery primitive):              │
        │    chord(                                              │
        │      group(                                            │
        │        render_page.s(doc_id, 0), ..., render_page.s(doc_id, N)  │
        │      ),                                                │
        │      chord(                                            │
        │        group(extract_page.s(doc_id, 0), ..., extract_page.s(doc_id, N)),│
        │        aggregate_document.s(doc_id)                    │
        │      )                                                 │
        │    )                                                   │
        │                                                       │
        │  Each task:                                            │
        │  • Idempotent (checks disk marker before doing work)  │
        │  • Writes durable artifacts BEFORE ack'ing             │
        │  • Updates Redis job state atomically                  │
        │  • Retries on transient errors (Celery built-in)       │
        │  • Dead-letter queue on terminal failure               │
        └───────────────────────────────────────────────────────┘
                    │ writes immediately
                    ▼
        ┌───────────────────────────────────────────────────────┐
        │  Filesystem (NFS / S3 / local disk)                   │
        │  data/output/{doc_id}/                                │
        │    run_manifest.json        ← atomic write-then-rename│
        │    pages_png/page_NNN.png                             │
        │    pages/page_NNN.md        ← NEW, written per-page   │
        │    pages/page_NNN.json      ← NEW, full PageState dict│
        │    tables/page_NNN_table_MM.{json,md}  ← NEW per-page │
        │    entities/page_NNN.jsonl  ← NEW per-page            │
        │    trace/page_NNN.json      ← NEW per-page            │
        │    full_document.md         ← written by aggregator   │
        │    entities.json            ← written by aggregator   │
        │    tables/merged_NN.json    ← written by aggregator   │
        └───────────────────────────────────────────────────────┘
                    │ (Phase 4+5: chunk + embed + Qdrant)
                    ▼
        ┌───────────────────────────────────────────────────────┐
        │  chunk_page.s  →  embed_chunks.s  →  upsert_qdrant.s  │
        │  Each step checkpointed. Re-runnable.                 │
        └───────────────────────────────────────────────────────┘
```

##### L1.1.d Task decomposition (Celery)

Each task type is small, idempotent, and writes its output before acknowledging the
message. Task granularity = **one page** for all per-page work, **one document** for
aggregation.

| Task | Input | Output (durable) | Idempotency check | Retry policy |
|---|---|---|---|---|
| `ingest_document.s` | `{doc_id, pdf_path}` | Manifest with pages_total; fires the chord | Manifest exists with `phase=started` | 3× exponential |
| `preprocess_doc.s` | `doc_id` | `baselines.json` + updates manifest | `baselines.json` exists | 3× |
| `render_page.s` | `(doc_id, page_num)` | `pages_png/page_NNN.png` | File exists with non-zero size | 5× (local work, transient fs hiccups) |
| `extract_page.s` | `(doc_id, page_num)` | `pages/page_NNN.{md,json}`, `tables/…`, `entities/…`, `trace/…` | `pages/page_NNN.json` has `final.locked=true` | 5× with jitter; DLQ on terminal |
| `aggregate_document.s` | `doc_id` | `full_document.md`, `entities.json`, `tables/merged_*.json`, manifest `phase=done` | Manifest `phase=aggregated` | 3× |
| `chunk_document.s` | `doc_id` | `chunks/chunks.jsonl` | File exists | 3× |
| `embed_chunks.s` | `(doc_id, chunk_batch)` | `embeddings/batch_NNN.jsonl` | Per-batch marker | 5× |
| `upsert_qdrant.s` | `(doc_id, batch)` | Qdrant point IDs recorded in manifest | Point IDs present in manifest | 5× with backoff |

**Why per-page Celery tasks instead of one big per-doc task:**
- A 700-page doc that fails at page 650 loses only page 650, not pages 0–649.
- Horizontal scale is automatic — 20 Celery workers × 4 child processes × 100 in-flight
  Gemini calls per process = ~8,000 pages in flight across the fleet.
- Retry granularity matches failure granularity (a single Gemini 500 retries just that
  page, not the whole doc).

##### L1.1.e Durability discipline — the "write before ack" rule

Every worker follows this sequence without exception:

```
1. Read idempotency marker. If present, ack and return.
2. Do the work (Gemini call, rasterize, etc.).
3. Atomic write of output file(s): write to `.tmp` then `os.replace()` to final name.
4. Update manifest via Redis HSET (atomic).
5. Log structured event.
6. Ack the message (Celery task returns cleanly).
```

If the worker crashes between step 3 and step 6, Celery redelivers the task. Step 1's
idempotency check sees the marker and short-circuits. **No duplicate work, no lost work.**

For the full-document aggregator the same pattern applies: write `full_document.md.tmp`
first, fsync, rename. A crashed aggregator leaves either the old file or the new file
— never a corrupted half-file.

##### L1.1.f Run manifest (source of truth on disk)

`data/output/{doc_id}/run_manifest.json`:

```json
{
  "job_id": "uuid",
  "doc_id": "gswa_harvey_1_wcr",
  "pdf_path": "data/uploads/GSWA Harvey 1 WCR.pdf",
  "pdf_sha256": "ab12...",
  "pages_total": 23,
  "created_at": "2026-04-24T10:00:00Z",
  "updated_at": "2026-04-24T10:08:32Z",
  "phases": {
    "preprocessing": {"status": "done", "ms": 4321, "ended_at": "..."},
    "rasterize":     {"status": "done", "ms": 12056, "pages_ok": 23},
    "extract":       {"status": "in_progress", "pages_ok": 17, "pages_failed": 0},
    "aggregate":     {"status": "pending"},
    "chunk_embed":   {"status": "pending"}
  },
  "errors": [],
  "schema_version": 1
}
```

The manifest is the authority. Redis mirrors the hot parts for fast API reads; the
filesystem manifest is the recovery ground truth. If Redis is wiped, a one-time scan
of `data/output/*/run_manifest.json` reconstructs the job registry.

##### L1.1.g Resume semantics

**On Celery worker restart:**
- Celery's broker (Redis) redelivers unacked messages. Per-page idempotency markers
  short-circuit the ones that already completed.
- No human intervention needed.

**On full cluster restart:**
- `POST /admin/resume` scans `data/output/*/run_manifest.json` for any manifest with a
  non-terminal phase (`in_progress` or `pending`).
- For each, re-enqueue the chord at the first non-done phase. Completed pages are
  short-circuited by their markers; only pending pages actually call Gemini.

**On user "retry failed pages":**
- `POST /ingest/{job_id}/retry-failed` reads manifest, finds pages with `pages_failed`
  entries, re-enqueues only those.

##### L1.1.h Redis as broker + state store — specifics

- **Broker queue**: durable (`delivery_mode=persistent`), per-task-type routing keys
  so Gemini tasks can be isolated on a dedicated worker pool.
- **Acks late** (`task_acks_late=True`) + **reject on worker lost** — guarantees that a
  task killed by OOM or SIGKILL is redelivered, not silently dropped.
- **Visibility timeout** long enough for the slowest page (e.g., 5 min for an escalated
  Pro call with thinking). Too short → duplicate execution; too long → slow failover.
- **Task results**: short TTL (1 hour). The manifest is the permanent record; Celery's
  result backend is only for live polling.
- **Job live state**: `HSET jobs:{id} phase pages_done pages_total last_error_page`.
  Written by workers, read by the FastAPI `/ingest/{id}` endpoint. Fast, always fresh.

**Redis sizing (scale math):**
- 100 concurrent PDFs × 700 pages = 70,000 in-flight tasks worst case.
- At ~200 bytes/task metadata = 14 MB of queue state. Redis handles this comfortably
  with default memory.
- Backup: Redis AOF (append-only file) with `fsync=everysec`. Losing ≤1s of job state
  on crash is acceptable because the filesystem manifest is the ground truth.

##### L1.1.i Rate limiting & back-pressure

Gemini Tier 3 is not infinite. At 100 PDFs × 700 pages × 2 calls/page = 140,000 API
calls — if issued flat-out, that's 2.3k RPS, well above the 10k RPM limit (≈167 RPS).

Mitigation:
- **Per-model Celery rate limit** (`@task(rate_limit="150/s")`) enforced by the broker.
- **Concurrency cap at the client** (already: `Semaphore(100)` in `GeminiClient`).
- **Adaptive throttle**: on HTTP 429, Celery task retries with Celery's `retry_backoff`
  + `Retry-After` header respect. One misbehaving document cannot block the whole fleet.
- **Job priority queues** — interactive uploads get higher priority than bulk backfills.

##### L1.1.j Failure taxonomy & where each is handled

| Failure | Detection | Recovery |
|---|---|---|
| Transient Gemini 500/429/timeout | Exception class / HTTP code | Celery retry w/ backoff, jitter |
| Page extraction permanently fails (bad PDF page) | Retries exhausted | DLQ entry, page marked `failed` in manifest; user can retry via `/retry-failed` |
| Worker OOM killed mid-page | Broker redelivers unacked msg | Idempotency marker short-circuits completed work; partial write discarded via `.tmp` rename pattern |
| Whole machine reboot | Broker persists, disk persists | Workers restart, idempotency shields completed pages, missing pages re-enqueued |
| Redis crash | AOF replay on restart | Manifest on disk is ground truth; optional `/admin/reconcile` endpoint rebuilds Redis state from disk |
| Disk full | Write fails | Task raises; Celery retries; monitoring alerts on `aggregate failed pages` metric |
| Gemini quota blown | 429 storm | Rate limiter throttles; queue backs up; ops alert; no data loss (messages stay in queue) |
| Pipeline bug wipes all output | Irrecoverable | Backups: daily rsync of `data/output/` to cold storage; Redis AOF snapshots |

##### L1.1.k Pros & Cons (Celery + Redis vs alternatives)

**Pros of Celery + Redis:**
1. **Durable by design.** Broker persistence + ack-late + idempotency = zero-data-loss
   guarantee without writing our own queue.
2. **Battle-tested.** Celery powers production pipelines at Instagram, Mozilla,
   Airbnb — we're on the well-worn path.
3. **Horizontal scale.** Add workers = add throughput. No code changes.
4. **Retry / DLQ / rate-limit / priority** all built in. We don't reinvent them.
5. **Observability ecosystem** — Flower, Prometheus exporter, Celery events.
6. **Task composition** (chords, chains, groups) is exactly the ingestion pipeline's
   shape: per-page fanout → aggregate fan-in.
7. **Decoupled** from the web tier — FastAPI scales and restarts independently of
   workers.

**Cons / costs:**
1. **Operational overhead.** Redis + worker processes + Celery beat (for scheduled
   tasks) = three more things to run, monitor, and back up.
2. **Task signature ossification.** Changing a task's signature after deploy requires
   either blue/green queues or a forced drain + upgrade window.
3. **Debugging harder.** A stack trace is on a worker, not in the web request. Need
   centralized logging (stdout + structlog JSON + a log aggregator) from day one.
4. **Memory tuning.** Celery default worker model is prefork — each worker process
   loads the whole app (LangGraph, fitz, genai client). On a dev laptop, 4 workers =
   ~2 GB RAM floor. Mitigation: use `--concurrency=N` + `--pool=solo|threads|gevent`
   per workload. Gemini async calls + threads pool is likely the best fit.
5. **Not free.** On managed infra (AWS ElastiCache + ECS tasks), this is a real
   monthly line item. For self-hosted / pilot it's free but adds a service to the
   compose file.
6. **Redis is AP, not CP.** On a network partition you can lose ≤1s of queue state
   (AOF everysec). Acceptable because disk is authoritative.
7. **Ordering is per-queue, not per-task.** We don't need ordering (pages are
   extracted in parallel anyway), but if a future feature requires it, we'd need
   Redis Streams or a different broker.

##### L1.1.l Alternatives considered

| Option | Pro | Con | Verdict |
|---|---|---|---|
| **Celery + Redis** | Mature, durable, well-known | Two new services | **Default.** |
| **Celery + RabbitMQ** | Stronger delivery guarantees, better routing | Heavier ops; AMQP learning curve | If we ever need strict message ordering or complex topology, revisit. |
| **Dramatiq + Redis** | Simpler than Celery, same guarantees | Smaller ecosystem | Viable; pick if team dislikes Celery's complexity. |
| **RQ + Redis** | Simplest possible | No chords, no chains, no complex canvases | Insufficient — we need the chord primitive for fan-out/fan-in. |
| **Arq + Redis** | Async-native, Pythonic | Younger, smaller community | Strong candidate if we want pure asyncio workers. |
| **Temporal / Cadence** | Durable workflow engine, replay semantics | Steeper learning curve; separate server to run | Overkill for now; revisit at Phase 5 if workflow complexity grows. |
| **Prefect / Dagster** | Good DAG UX, observability | More DevOps surface, opinionated about data pipelines | Good for ETL; our workload is per-doc streaming, not table-pipeline shaped. |
| **Roll our own with asyncio + SQLite** | Zero new services | We'd reinvent every Celery feature badly | **Do not.** Worth doing only if we consciously cap scale at 1 machine. |
| **Just write per-page to disk, keep BackgroundTasks** | Minimal change | Still loses jobs on process restart; no horizontal scale | **Tier 1 interim fix** (see §L1.1.m). Real fix is Celery. |

##### L1.1.m Rollout — three tiers, each independently shippable

The architecture above is the target. It can be adopted in three increments, each
closing a class of data-loss risk without blocking the next.

**Tier 1 — Per-page durability (no new services).**
- Worker writes `pages/page_NNN.md`, `pages/page_NNN.json`, `tables/…`,
  `entities/page_NNN.jsonl`, `trace/page_NNN.json` immediately after `graph.ainvoke`
  returns.
- Phase 3 postprocessor rewritten as an **aggregator** that reads from disk.
- Run manifest written and updated.
- `FastAPI BackgroundTasks` retained for now.
- **Closes F1, F2, F6** (mid-run kill no longer wipes output; aggregator failure
  doesn't wipe extracted pages; partial resume is possible via manifest inspection).
- **Leaves F3, F4** (process restart still loses live job registry; no supervision).
- No new dependencies. One PR.

**Tier 2 — Durable job registry (SQLite, still no broker).**
- Replace `_jobs: dict` with a SQLite table `ingestion_jobs` (id, status, timings,
  error, output_dir, created_at, updated_at).
- On app startup, reconcile: any job with `status=in_progress` and no recent manifest
  update → mark `interrupted`, expose via API.
- `POST /ingest/{id}/resume` → re-reads manifest, re-invokes pipeline from first
  non-done phase.
- **Closes F3** (job history survives restarts).
- Still uses `BackgroundTasks` → F4 open.
- One new dep (SQLAlchemy is already planned for L3/L4).

**Tier 3 — Celery + Redis.**
- Full target architecture per §L1.1.c.
- Replace `BackgroundTasks` with Celery enqueue.
- Add Redis service to `docker-compose.yml`.
- Add `celery_worker/` and `celery_app.py`.
- Per-page tasks with idempotency markers, ack-late, DLQ.
- **Closes F4, F5** (supervision, retries, memory bounded to one page per task).
- Unlocks the 100 PDFs × 700 pages scale target.

**Ordering rationale:** Tier 1 is high-leverage and low-risk — ship it immediately.
Tier 2 is only needed when real users are polling job status across restarts. Tier 3
is only needed when you're actually ingesting >5 PDFs in parallel or documents are
>100 pages. For today's pilot (one doc at a time, dev laptop), Tier 1 is sufficient.
The architecture above guarantees we never rewrite — each tier builds on the last.

##### L1.1.n Pitfalls to avoid during the Celery migration

1. **Don't put the Gemini semaphore inside the worker process.** `Semaphore(100)` per
   worker × 4 workers = 400 concurrent Gemini calls, blowing past rate limits. The
   concurrency cap belongs at the **cluster** level (Celery rate limit + per-queue
   concurrency) or at a shared token-bucket in Redis.
2. **Don't serialize `PageImage` with `png_bytes` in the Celery task payload.** PNG
   bytes are 200-600 KB; pushing them through the broker is wasteful and adds
   serialization cost. The PNG is already on disk — tasks pass `(doc_id, page_num)`
   and read the file.
3. **Don't store Gemini responses in the Celery result backend.** Results go to disk
   (the per-page JSON). The result backend is only for task status/completion.
4. **Don't rely on `task.delay()` return values for business logic.** Chain via chords
   and look at the manifest/Redis job state, not task results.
5. **Don't enable `--reload` on the Celery worker.** It silently breaks prefork
   semantics. Use `watchmedo auto-restart` or `celery multi` for dev restarts.
6. **Don't share database sessions across Celery forks.** SQLAlchemy session must be
   created per-task (or per-worker-child), never at module import time.
7. **Beware of Celery's default `prefetch_multiplier=4`.** For long-running tasks (our
   case: per-page can be 30s+), prefetch holds messages on a busy worker while others
   are idle. Set `worker_prefetch_multiplier=1` for Gemini tasks.
8. **Monitor the DLQ.** A silent DLQ accumulating failed pages is worse than a visible
   alert. Wire a `/admin/dlq` endpoint + a daily report.
9. **Don't skip the atomic `.tmp` rename pattern.** Direct writes can leave
   half-written files on a SIGKILL. `write → fsync → rename` is the only safe pattern
   for durable output.
10. **Plan for clock skew across workers.** If timings feed SLA calculations, use a
    single source (the web tier's monotonic clock) and propagate it in task args.

##### L1.1.o Green-light checklist for each tier

**Tier 1** — ship now:
- [ ] Worker writes per-page MD/JSON/tables/entities/trace on completion.
- [ ] Run manifest written and incrementally updated.
- [ ] Postprocessor rewritten as aggregator that reads from disk.
- [ ] Idempotent re-aggregation (re-running produces byte-identical outputs).
- [ ] Tests: kill-the-worker-mid-doc test; aggregator-only test; resume-from-disk test.

**Tier 2** — when users complain about jobs disappearing on restart:
- [ ] SQLite `ingestion_jobs` schema + migration.
- [ ] Startup reconciliation against disk manifests.
- [ ] `/admin/resume` and `/ingest/{id}/retry` endpoints.

**Tier 3** — when concurrent-docs > 5 or pages-per-doc > 100 regularly:
- [ ] Redis + Celery in docker-compose.
- [ ] Per-page task + idempotency markers.
- [ ] Chord-based pipeline canvas.
- [ ] Rate limiting (cluster-level, not per-worker).
- [ ] DLQ monitoring + retry admin endpoints.
- [ ] Flower or Prometheus exporter wired.
- [ ] Load test: 10 × 100-page PDFs concurrent, zero data loss on force-killed worker.

---


### L2 — Domain Knowledge Layer

`app/domain/well_taxonomy.py` (new). Controlled vocabulary as Python enums + JSON
schemas, exported into Gemini `responseSchema`:

- **NPT categories** — IADC standard 26-code list (mechanical, wait-on-weather,
  wait-on-cement, stuck pipe, lost circulation, etc.)
- **Operation types** — drilling, tripping, casing, cementing, logging, completion,
  workover.
- **Depth types** — MD, TVD, KB, RT, MSL, AHBDF.
- **Unit normalization** — ft↔m, ppg↔kg/m³, bbl↔m³, psi↔kPa, in↔mm, °F↔°C.
- **Section types** — surface, intermediate, production, completion.
- **Formation aliases** — regional naming variants per asset config.
- **Artifact classes** — the 10 PRD §10 classes (master, program, offset, DDR, handover,
  service, risk, action, learning, media).

**Entity resolution module** — `app/domain/entity_resolver.py`:

- Each new entity → embedded → top-K against canonical store
- If sim > 0.92 AND fuzzy string > 0.85 AND domain rules agree → merge to canonical ID
- Otherwise create new canonical with provenance trail
- Stub now (just log candidates), activate when corpus reaches 2+ docs

L2 is the **single biggest accuracy lever after L1** because it constrains the model's
output space at generation time rather than trying to clean up free text afterward.

---

### L3 — Tenant & Hierarchy Model

Entity model per PRD §4.2. Stored in **PostgreSQL** (or SQLite for pilot — same SQLAlchemy
schema):

```
Operator ─┬─ Asset ─┬─ Field ─┬─ Campaign ─┬─ Rig
          │         │          │             └─ Well ─┬─ Section
          │         │          │                       └─ OperationWindow ─ Event ─ Action/Risk/Lesson
          │         │          └─ User (with scope membership)
          │         └─ Taxonomy override
          └─ Tenant config (timezone, units, naming, AI guardrails)
```

PRD-required behaviors:
- One artifact → one primary scope, optional explicit cross-link (FR-007)
- Risk inheritance with local override (FR-024)
- Action ownership single-writer with watchers (PRD §4.3)
- Audit log on every mutation (FR-039)

**Pilot simplification:** ship with one operator, one asset, one campaign hard-coded.
Schema supports multi-tenant from day one but UI/RBAC can be deferred to Phase 3.

---

### L4 — Artifact Linkage & Versioning

The ingestion service (L1) becomes one of several artifact producers. New SQLAlchemy
table `artifacts` with PRD-mandated metadata (FR-005): `artifact_class`, `source_owner`,
`scope_path`, `effective_date`, `confidentiality_class`, `revision_no`,
`supersedes_id`, `release_state`, `sha256`, `link_to_extraction_run_id`.

**Revision logic (FR-006):** uploading a new version of an existing artifact (matched by
`scope + class + filename` or explicit override) marks prior revision `superseded` but
keeps it queryable. Retrieval by default filters to current effective revision.

**Duplicate detection (FR-008):** on upload, compute sha256 + content embedding; flag
high-similarity matches for steward review.

---

### L5 — Retrieval Backbone (RAG + KG)

Two parallel indexes from day one (do **not** bolt KG on later — it's a 10× cost):

#### 5.1 Qdrant `chunks_text`
- Heading-aware MD chunking: `MarkdownHeaderTextSplitter` + `RecursiveCharacterTextSplitter`
- ~1000 token chunks, 200 overlap
- Payload: `doc_id`, `artifact_class`, `scope_path` (well/section), `page_num`,
  `section_path` (heading hierarchy), `chunk_index`, `text`, `has_table`, `has_image`,
  `tier_weight`, `provenance` (full L1 record)
- Vector: `gemini-embedding` (768-dim or model-default)
- Indexed payload fields: `scope_path`, `artifact_class`, `section_path`,
  `effective_date`, `release_state`

#### 5.2 Qdrant `entities_facts`
- One point per L2 entity (NPT event, formation top, depth, fluid, BHA component, etc.)
- Embedding of canonical text form: `f"{entity_type} {value} {unit} {context_phrase}"`
- Payload: structured fields + provenance + `canonical_id` (from L2 resolver)
- Indexed fields: `well_api`, `formation`, `npt_category`, `depth_range`, `event_date`
- Enables filtered retrieval like "all NPT > 100h in Eagle Ford in 2024" without LLM

#### 5.3 Qdrant `image_descriptions`
- One point per described image/figure/log strip from L1
- Embedding of description text
- Payload: `doc_id`, `page_num`, `bbox`, `description`, `image_type` (log_strip,
  schematic, plot, photo, table_image), `tier_weight`
- Used for the image-grounding pipeline (§3.2)

#### 5.4 Knowledge Graph (graph DB)
Choice: **Neo4j Community** for pilot (Docker), or **NetworkX-on-Postgres** for ultra-light.
Either way the on-disk format is `graph.jsonl` per the entity-extraction output, so the
graph store is interchangeable.

Node types: `Operator`, `Asset`, `Field`, `Campaign`, `Well`, `Section`, `Formation`,
`Rig`, `BHA`, `NPTEvent`, `Lesson`, `Action`, `Risk`.

Edge types: `BELONGS_TO`, `PENETRATES` (well→formation with depths), `EXPERIENCED`
(well→NPT), `RAN_ON` (BHA→well), `LEARNED_FROM` (lesson→event), `ANALOG_OF`
(well→well, with similarity score + reasons), `MITIGATES` (action→risk).

KG enables:
- **Offset intelligence** (PRD FR-012): "wells in same field within ±500m TVD with same
  formation sequence" → graph traversal instead of vector search
- **Repeated-issue detection** (PRD FR-013): NPT events grouped by category + formation
  + section type
- **Cross-well comparison** (the user's stated future goal)

#### 5.4.1 Neo4j Integration — Deep Dive (design-only, no code yet)

> **Status:** Not built. This subsection is the full integration dossier to be consulted
> when the KG work is actually picked up. The explicit ordering is:
>
> **(1) Clean extraction (L1)** must be locked first →
> **(2) Entity validation & resolution (L2)** must be locked second →
> **(3) Relationship tagging & Neo4j load (this section)** is layered on top **last**.
>
> Any Neo4j work started before (1) and (2) reach their exit gates will poison the graph
> with hallucinated nodes and ambiguous edges that are effectively impossible to clean up
> without wiping the store. **Do not deviate from this order.**

##### 5.4.1.a Why Neo4j (and why later, not now)

The product value of the KG is **cross-document, cross-well reasoning** — offset intel,
repeated-cause detection, lesson analogs, "all wells that hit Eagle Ford in <500m TVD
with a stuck-pipe event". None of that is available from a vector index alone because:

- Vector search retrieves **text similarity**, not **structural relationships**. It cannot
  answer "find wells that share a formation *sequence*" — that's a path query.
- Aggregations like "total NPT hours by IADC category across the Field" are graph/SQL
  problems, not embedding problems.
- Analog-well ranking needs **multi-hop** traversal (well → field → neighboring wells →
  same-section NPT history → lessons referencing that NPT) — each hop filtered by scope
  and tier weight.

But the value is **strictly downstream** of extraction accuracy. A graph built from
noisy entities is worse than no graph — it produces confidently-wrong analogs and
miscounts NPT campaigns. Hence the ordering above.

##### 5.4.1.b Prerequisites (hard gates before any Cypher is written)

These are the **go/no-go conditions**. Failing any of them means Neo4j work is on hold.

| Gate | Source | Threshold | Why it blocks the KG |
|---|---|---|---|
| G-KG.1 | Phase 1A exit gate | Entity recall ≥ 0.99 (depths/NPT/tables) | Missed entities = missing nodes. A well with 40% of its NPTs missing is worse than no KG record at all. |
| G-KG.2 | Phase 1B exit gate | Unit & NPT-code conformance = 100% | Free-text category strings produce duplicate/ambiguous nodes (e.g., "stuck pipe" vs "Stuck Pipe" vs "SP"). |
| G-KG.3 | Phase 1B — entity resolver | Canonical-ID collision rate ≤ 1% on dev corpus | Without resolution, "Well ABC-1", "ABC 1", "ABC1" become three separate nodes. |
| G-KG.4 | Phase 1A — provenance | 100% of entities carry `{doc_id, page_num, bbox}` | Every edge must be auditable back to a page region; without provenance, KG becomes an unaccountable black box. |
| G-KG.5 | L2 taxonomy | Closed enum for every relationship type that maps to a PRD FR | Ad-hoc relationship strings defeat traversal queries (`ANALOG_OF` vs `analog_of` vs `ANALOGOUS_TO`). |
| G-KG.6 | Integration test | Re-running the loader on the same corpus is **idempotent** (MERGE-only, no duplicate edges) | Non-idempotent loaders corrupt the store after the first re-ingestion. |

Only when G-KG.1 through G-KG.5 are green does Neo4j infra stand up. G-KG.6 is a design
requirement for the loader itself.

##### 5.4.1.c Ingestion flow (where Neo4j plugs in)

```
  L1 ingestion pipeline (existing)
        │
        ▼
  Per-page extraction output
  {markdown, tables, images, entities[], bboxes}
        │
        ▼
  L2 entity resolution  ──► canonical_id assigned to every entity
        │                    provenance attached
        ▼
  Post-processor writes
    • full_document.md
    • tables/*.json
    • entities.json (existing)
    • relationships.jsonl (NEW, see §5.4.1.d)
        │
        ▼
  KG Loader (new module, activated only after gates pass)
    • Reads entities.json + relationships.jsonl per doc
    • MERGE nodes by canonical_id
    • MERGE edges with properties (source provenance, confidence, tier)
    • Idempotent: re-running the loader on the same doc is a no-op
        │
        ▼
  Neo4j store
```

The loader is an **out-of-band batch** keyed by `doc_id` — it runs after the L1 pipeline
completes, not inside it. This keeps Neo4j a non-blocking downstream consumer: if Neo4j
is down, ingestion still completes, and the loader can back-fill.

##### 5.4.1.d Relationship tagging — where it is produced

**Decision: relationships are produced by a dedicated post-extraction pass, NOT asked of
the page-level extractor.**

Rationale: the extractor sees ONE page at a time. Most meaningful relationships span
pages or documents (a well penetrates a formation described on page 3 at a depth logged
on page 14, referenced in a lesson captured in a different report entirely). Asking the
page extractor for relationships would force it to guess cross-page structure it cannot
see, producing noisy edges.

Instead: a **Relationship Builder** module runs after L2 entity resolution. It has three
inputs:

1. The full-document entity list (with canonical IDs and provenance).
2. The L2 controlled-vocabulary schema defining valid `(subject_type, edge_type, object_type)`
   triples.
3. A small rule library (deterministic, rule-based first; LLM-assisted only for edge
   cases):
   - `PENETRATES` — emitted when a `Well` entity and a `Formation` entity co-occur with
     a `depth` value in the same section or table row.
   - `EXPERIENCED` — emitted when an `NPTEvent` co-occurs with a `Well` in the same DDR.
   - `RAN_ON` — emitted when a `BHA` and `Well` co-occur in a BHA run summary.
   - `ANALOG_OF` — NOT emitted by the extractor or builder. Computed at query time by the
     retrieval layer using well attributes + traversal — storing it would freeze an
     analog judgement that should adapt as new wells are ingested.
   - `LEARNED_FROM` — emitted when a `Lesson` entity explicitly references an event or
     NPT by canonical ID.
   - `MITIGATES` — emitted when an `Action` entity's `addresses` field points to a `Risk`.

Every emitted relationship carries:
- `{source_doc_id, source_page_num, source_bbox}` — provenance
- `confidence` — from the extraction pass that produced the constituents
- `tier_weight` — inherited from the source artifact's tier (§3.3)
- `extraction_run_id` — for traceability to a specific ingestion

LLM-based relationship extraction is held in reserve as a **second pass only**, gated
behind explicit review, for rare relationships the rule library cannot express. It is
**not** the default — deterministic rules over validated entities produce fewer false
edges than LLM relationship extraction over the same entities.

##### 5.4.1.e Neo4j schema (indicative — to be finalized in the KG phase)

```
// Core hierarchy (mirrors L3 tenant model)
(:Operator)-[:OWNS]->(:Asset)-[:CONTAINS]->(:Field)-[:RUNS]->(:Campaign)
(:Campaign)-[:DRILLS]->(:Well)-[:HAS_SECTION]->(:Section)
(:Campaign)-[:USES_RIG]->(:Rig)

// Geology
(:Well)-[:PENETRATES {top_md, top_tvd, bottom_md, bottom_tvd, confidence, provenance}]->(:Formation)

// Operations
(:Well)-[:EXPERIENCED {duration_hr, depth_md, iadc_code, confidence, provenance}]->(:NPTEvent)
(:BHA)-[:RAN_ON {section, run_no, provenance}]->(:Well)

// Learning
(:Lesson)-[:LEARNED_FROM {provenance}]->(:NPTEvent)
(:Lesson)-[:APPLIES_TO]->(:Section)    // section type, not instance
(:Action)-[:MITIGATES {status, owner}]->(:Risk)
(:Risk)-[:OBSERVED_ON]->(:Well)

// Artifact lineage (ties graph back to source docs)
(:Document)-[:MENTIONS {page_num, bbox, confidence}]->(:NPTEvent)
(:Document)-[:MENTIONS {page_num, bbox, confidence}]->(:Formation)
// etc. — every node type has a MENTIONS edge from at least one Document.
```

**Indexing strategy:**
- Uniqueness constraints on `canonical_id` for every node type.
- Composite index on `(Well.field_name, Well.tvd_range)` for offset queries.
- Composite index on `(NPTEvent.iadc_code, NPTEvent.formation_name)` for repeated-cause.
- Full-text index on `Well.name_aliases[]` for entity-resolution fallback searches.

##### 5.4.1.f Pros & Cons

**Pros:**
1. **Relationship queries become first-class.** Offset intelligence, repeated-cause
   detection, and analog ranking shift from brittle LLM prompting to deterministic
   Cypher — orders of magnitude faster and auditable.
2. **Audit trail by design.** Every edge carries `{doc_id, page_num, bbox}`. An SME can
   click any relationship in the UI and see the exact page region that produced it.
3. **Schema enforces correctness.** Typed nodes + typed edges + uniqueness constraints
   prevent the "same well, three different IDs" drift that pure-vector stores cannot
   catch.
4. **Decoupled from ingestion.** Neo4j down ≠ ingestion down. Loader is a batch job that
   back-fills when the store returns.
5. **Portable format.** The on-disk `relationships.jsonl` is graph-store-agnostic. If we
   outgrow Neo4j Community or move to AWS Neptune/TigerGraph/AGE, the migration is a
   re-load, not a re-extraction.
6. **Enables SME "why did the system say this?" at the relationship level** — not just
   at the document level.

**Cons / costs:**
1. **Operational overhead.** Neo4j is another service to run, back up, monitor, and
   upgrade. Not free even in Community edition.
2. **Schema ossification.** Once the graph has tens of thousands of edges, schema
   migrations (renaming an edge type, splitting a node type) are painful and error-prone.
3. **Double-writing risk.** Artifacts live in Postgres (L4), entities live in Qdrant (L5),
   graph lives in Neo4j — keeping three stores consistent after a re-ingestion or a
   retraction is a known hard problem. Requires a tombstone/lineage discipline.
4. **Learning curve.** Cypher is a specialized DSL. Team onboarding cost is non-zero.
5. **Licence footprint.** Community edition is GPLv3 — fine for a self-hosted internal
   product, but if we ever sell a hosted multi-tenant service, enterprise licensing
   becomes a real cost.
6. **Not useful at single-doc scale.** KG value compounds with corpus size. Building it
   before we have ≥ 50 ingested wells is pure overhead.

##### 5.4.1.g Future pitfalls we must plan against

These are the traps other teams have stumbled into. Calling them out now so the KG phase
does not re-learn them the hard way.

1. **"Entity explosion."** If the extractor or L2 resolver has a bug that produces a
   near-duplicate `Well` node per DDR, the graph doubles in size with every ingestion.
   *Mitigation:* G-KG.3 (≤ 1% collision rate on dev corpus before loader goes live),
   plus a nightly deduplication audit that flags `Well` pairs with ≥ 0.92 name similarity
   as candidates for merge review.
2. **"Poisoned edges from bad pages."** A single mis-extracted page can emit dozens of
   wrong `PENETRATES` edges (a formation top misread). Since edges carry provenance,
   **retraction** must be supported: a UI or admin tool that accepts `doc_id` and
   deletes all graph changes whose `extraction_run_id` matches.
3. **"Silent analog drift."** If the `ANALOG_OF` edge is stored (not computed at query
   time), it freezes an analog judgement that becomes stale as new wells arrive.
   *Mitigation:* keep `ANALOG_OF` a **derived** relationship — computed on demand by the
   retrieval layer, cached for a short TTL only. Never written to the graph as a
   permanent edge.
4. **"Schema rewrite nightmare."** Adding a new node type is cheap; renaming or splitting
   an existing type when there are 10M edges is not. *Mitigation:* every node type gets
   a `schema_version` property from day 1. Loader writes the version. Readers tolerate
   older versions. Migrations are additive.
5. **"Graph-RAG hallucination laundering."** The tempting pattern is "serialize a
   sub-graph and shove it into a Gemini prompt for synthesis." This turns structured
   fact into free text the model can re-interpret. *Mitigation:* the composer in the
   hybrid retrieval pipeline (§5.5) must quote graph facts **verbatim with provenance**,
   and the critic must flag any numeric claim in the draft that does not appear verbatim
   in the retrieved sub-graph.
6. **"Permissions bypass via graph traversal."** A scoped query on `chunks_text` correctly
   excludes restricted docs, but a graph traversal that starts from a public node can
   walk into restricted territory via confidential-sourced edges. *Mitigation:* every
   edge and node carries a `confidentiality_class`. L7 RBAC filter is applied at
   traversal time, not just at query entry.
7. **"Too-clever loader."** If the loader tries to auto-heal entity mismatches at load
   time (e.g., "this well looks like that other well, let me merge them"), it will
   eventually merge the wrong pair. *Mitigation:* the loader is **dumb** — it only
   creates what the upstream pipeline told it to create. Merges are explicit, auditable
   operations performed by a separate admin tool.
8. **"No retraction path."** If a source document is superseded (L4 revision) or
   retracted, there must be a clean way to remove its contributions from the graph.
   Since every edge carries `extraction_run_id`, this is a single Cypher DELETE keyed
   on that ID. Must be tested end-to-end before the loader is released.
9. **"Graph as ingestion critical path."** Tempting to add "write to Neo4j" synchronously
   into the ingestion pipeline. Don't. Neo4j availability would become a single point
   of failure for the entire document intake. Always a separate batch loader.
10. **"Over-ambitious first schema."** Shipping a 25-node-type, 40-edge-type schema on
    day one guarantees half of them are wrong. *Mitigation:* first load is the 8 node
    types and 5 edge types listed in §5.4. Everything else earns its place by being
    required for a specific PRD FR.

##### 5.4.1.h When to actually start building it

Green-light checklist — all must be true:

- [ ] Phase 1A exit gate met (extraction accuracy on golden set)
- [ ] Phase 1B exit gate met (entity validation, unit/NPT enums, resolver stub active)
- [ ] Dev corpus is ≥ 10 wells / 50 documents (enough to meaningfully exercise the graph)
- [ ] At least one concrete PRD FR requires graph traversal **right now** (FR-012 offset
      or FR-013 repeated-cause are the canonical triggers)
- [ ] Team has committed capacity for (a) Neo4j operations, (b) schema migrations, and
      (c) the retraction/audit tooling — not just the happy-path loader

Until all five are true, KG work is **documented, not built.** The entity output from
L1 + L2 already produces a `graph.jsonl` style artifact — that artifact is forward-
compatible with any graph backend, so no work is lost by waiting.

##### 5.4.1.i Alternatives (and why Neo4j is still the likely pick)

| Option | Pro | Con | Verdict |
|---|---|---|---|
| **Neo4j Community** (Docker) | Mature, Cypher, native graph algos, best SME ecosystem | Another service, GPLv3, ops burden | **Default choice.** Best fit for pilot & enterprise dev experience. |
| **Apache AGE** (Postgres extension) | Graph + relational in one store, no new ops | AGE's OpenCypher support is partial, less mature tooling, SME query skills don't transfer as cleanly | Strong candidate if team wants to minimise services. Revisit at Phase 2 kickoff. |
| **NetworkX in-process** | Zero ops, pure Python, fast for small graphs | Not a database — no persistence, no concurrent writers, collapses past ~10M edges | Good for the canonical-store *in-memory resolver cache*. Not a replacement for a real graph DB. |
| **AWS Neptune / TigerGraph / Memgraph** | Managed, scales | Cloud lock-in, cost, not needed for pilot | Defer until clear scale signal. |
| **No graph at all, use Postgres recursive CTEs on a relational edge table** | Simplest possible stack | Expressing 4+ hop traversals in SQL is painful and slow; no graph algos | Viable fallback if ops burden is the deciding factor. Document as escape hatch, do not ship by default. |

The decision is **revisited at the Phase 2 kickoff**, not now. Documenting the decision
here means the kickoff can be a one-meeting confirmation, not a research project.

#### 5.5 Hybrid Retrieval Pipeline
For a user query:

1. Detect query intent (classifier or simple keyword routing): factual, analog,
   trend, image-lookup, action/risk
2. Route:
   - factual → `chunks_text` semantic search + scope filter + tier reweight
   - analog → KG traversal seeded by current-well attributes + entity vector search
   - trend → SQL/graph aggregation over `entities_facts`
   - image → `image_descriptions` search → crop bbox from stored page PNG
   - action/risk → SQL on L4 action table + scope filter
3. Rerank: semantic_score × tier_weight × recency_boost × scope_proximity
4. Compose answer with **source-cited Markdown**: every claim ends with
   `[doc_X p.N]` and the response payload carries the structured provenance for the UI
   to render evidence cards
5. **Always include the top-1 supporting image crop** if any retrieved chunk has
   `has_image=true` or the query intent is image-related

This is implemented as a small LangGraph: `intent_router → retriever(s) → reranker →
composer → critic`. The **critic** node verifies that every numeric claim and every
named entity in the draft answer appears in the retrieved evidence; if not, it either
rewrites with hedging or returns "insufficient evidence" (PRD §18.3).

---

### L6 — Operational Synthesis Agents

Each PRD operational deliverable (§16) is a small LangGraph that consumes from L5 and L4:

| Agent | PRD ref | Inputs | Output |
|---|---|---|---|
| Daily Summary | FR-018 | Latest DDR + specialist reports + open actions for scope | Drafted summary + provenance + open-issue diff |
| Shift Handover | FR-020 | Current-shift events + open actions + carry-forward | Handover note + ack queue |
| Program QA | FR-015–017 | Program artifact + offset entities + lessons | Gap list with section grouping + evidence cards |
| Offset Intelligence | FR-012–014 | Current-well attributes | Ranked analog wells + lesson summary + section watchouts |
| Lessons Retrieval | FR-029–030 | Query or current section context | Applicable lessons with provenance |
| Action Board | FR-023–025 | L4 actions table | Filtered prioritized list |
| Campaign Review | FR-027–028 | All scoped entities + L4 events | Trends + repeated-cause analysis + exception list |

Each agent emits to a **Drafts table** (release state = `draft|in_review|released`)
satisfying PRD §13.3 (high-consequence outputs route to review, never auto-release).

---

### L7 — RBAC & Governance

PRD §7. Implemented as FastAPI dependency that resolves user → scope memberships →
filter applied to every L3/L4/L5 query. Three permission axes:

- **Action**: read, create/update, approve, admin
- **Scope**: asset, field, campaign, well, section
- **Confidentiality**: public, internal, restricted, third-party

Audit log table records every mutation + every released AI draft with evidence count
and approver (FR-039).

---

### L8 — API + Workspace UI

#### API surface (extension of current FastAPI)

```
# Existing
POST   /ingest                              upload + start extraction job
GET    /ingest/{job_id}                     job status

# L4
POST   /artifacts                           upload + classify + link to scope
GET    /artifacts                           filtered list
GET    /artifacts/{id}/page/{n}             rendered page image (with optional bbox crop)

# L3
GET    /scopes/{type}/{id}                  hierarchy lookup
GET    /wells/{id}/timeline                 events + reports + actions sorted

# L5 retrieval
POST   /search                              {query, scope, intent_hint?} → answer + evidence + image crops
POST   /offsets/{well_id}                   ranked analog wells + reasons
POST   /program-qa/{program_id}             gaps + evidence cards

# L6 deliverables
POST   /summary/daily/{scope}               draft daily summary
POST   /handover/{shift_id}                 draft handover
GET    /actions                             open issue board

# L7
GET    /me                                  scopes + permissions
GET    /audit                               audit trail (admin)
```

Every retrieval/synthesis response shape includes:
```json
{
  "answer_markdown": "...",
  "evidence": [{"doc_id":"...","page_num":13,"bbox":[...],"excerpt":"...","tier":1}],
  "image_crops": [{"doc_id":"...","page_num":13,"bbox":[...],"png_uri":"..."}],
  "confidence": 0.91,
  "warnings": []
}
```

#### UI (deferred — pilot can use Swagger + a thin Streamlit/Next.js view)
The PRD UI (workspace tabs, dashboards) is large. For pilot we ship:
- Streamlit app with: scope picker → search box → answer panel with evidence cards
  (clickable → opens page PNG with bbox highlighted) → image crop strip below answer
- Static page-image viewer endpoint for evidence drilldown
- Full PRD workspace UI is Phase 4

---

## 5. Phase-Wise Roadmap (feasibility-ranked)

Each phase ends with a measurable accuracy/grounding gate. **No phase advances until
the gate is met.** Time estimates omitted intentionally — quality gates the schedule.

### Phase 1A — Accuracy & Grounding Hardening (current code base)
**Why first:** RAG/UI accuracy is bounded by extraction accuracy. Build the
golden-set + close the L1 gaps before adding more layers.

Scope:
1. G-L1.1 Persist page PNGs to `data/pages/`
2. G-L1.2 Extend extractor schema with bbox per table & per image
3. G-L1.3 Add `entities` field to extractor output (initially loose schema, tightened
   in Phase 1B once L2 vocab is defined)
4. G-L1.4 Adaptive DPI based on Phase 0 text density
5. G-L1.5 ROI second-look on validator failure
6. G-L1.6 Numeric guardrail pass
7. **G-L1.7 Durable, resumable ingestion** — per §4.L1.1 dossier. Tier 1 (per-page
   durability + run manifest + aggregator-only Phase 3) is **in-scope for Phase 1A**;
   Tier 2 (SQLite job registry) and Tier 3 (Celery + Redis) are deferred to the phase
   where real concurrent ingestion demand appears. Rationale: zero-data-loss for the
   golden-set build is a hard prerequisite — losing a 700-page extraction run is
   unacceptable regardless of accuracy.
8. **Build the golden set**: 50 pages drawn from 5 representative well reports,
   hand-labeled for: every depth, every NPT event, every table cell, every image
   description, every formation top
9. **CI regression test**: pytest fixture runs extraction on golden set; precision/recall
   per entity type reported; fail if any class drops below 0.95 (target 0.99)

**Exit gate:** golden-set entity recall ≥ 0.99 for depths/NPT/tables; ≥ 0.95 for image
descriptions; bbox IoU ≥ 0.7 on tables; **and** a kill-the-worker-mid-doc test proves
zero data loss on all already-extracted pages.

### Phase 1B — Domain Knowledge Layer (L2)
1. Build `well_taxonomy.py` enums (NPT IADC codes, depth types, units, op types)
2. Build `unit_normalizer.py` with bidirectional conversions
3. Tighten extractor `responseSchema` to use enums (NPT category, depth type, unit)
4. Build `entity_resolver.py` stub + canonical-store schema (Postgres)
5. Re-run golden set through schema-constrained extractor; expect entity precision lift

**Exit gate:** unit and NPT-code conformance = 100% (no free-text categories);
entity precision ≥ 0.99.

### Phase 2 — Retrieval Backbone (L5)

> **Ordering constraint (strict):** Phase 2 begins **only after Phase 1A and Phase 1B
> exit gates are green** and the KG prerequisite gates in §5.4.1.b (G-KG.1–G-KG.5) are
> met. Inside Phase 2 the sub-steps are also ordered: vector retrieval first (#1–#4),
> KG second (#5–#6). Do not start Neo4j work until the vector pipeline is citing
> sources correctly.

1. Stand up Qdrant (Docker compose) + Postgres
2. Build chunker + ingestion-to-Qdrant pipeline (3 collections)
3. Build hybrid retrieval LangGraph with citation + image grounding
4. Critic node: numeric/entity claim verification against retrieved evidence
5. Stand up Neo4j + graph-loader from L1 entity output (see §5.4.1 dossier)
6. Implement offset intelligence (KG traversal) + program QA (gap detection)
7. Source-citation API contract finalized
8. **Build retrieval golden set**: 30 questions across factual / analog / trend / image
   intents, each with hand-labeled correct answer + required evidence docs

**Exit gate:** retrieval precision@5 ≥ 0.9 on golden questions; **100% of answers
cite at least one source**; image-intent queries return correct cropped image
≥ 0.85 of the time; critic blocks ≥ 95% of injected hallucination test cases.

### Phase 3 — Tenancy, Artifacts, RBAC (L3 + L4 + L7)
1. Postgres schema: tenant hierarchy, artifacts, revisions, actions, risks, lessons,
   audit log
2. Migrate ingestion to write artifact + scope linkage
3. Implement RBAC dependency in FastAPI; scope filter injected into every L5 query
4. Approval workflow + release states for all L6 deliverables
5. Audit log surfaced via `/audit` endpoint

**Exit gate:** unauthorized scope queries return zero results; every released AI draft
has owner + evidence count + audit row; revision lineage retrievable.

### Phase 4 — Operational Synthesis Agents (L6)
1. Daily summary, shift handover, action board (highest PRD value per §20)
2. Lessons retrieval, offset intelligence pack, program QA pack
3. Each agent emits to Drafts table with confidence + evidence + warnings
4. **Synthesis golden set**: 10 historical handovers + 10 daily summaries hand-rated
   by SME; system drafts compared

**Exit gate:** SME usability rating ≥ 80% on drafted handovers + summaries
(PRD §22.1 SME trust score target); zero critical-issue carry-forward loss across
shift transitions in pilot.

### Phase 5 — Pilot UI + Rollout
1. Streamlit/Next.js workspace: scope picker, search, evidence cards, image viewer,
   drafts review queue, action board
2. Pilot data pack ingested per PRD §18.1 (1 well register, 10–20 offsets, 10–20 DDRs,
   10+ handovers, lessons archive)
3. 10–15 pilot users across roles per PRD §21.1
4. Baseline-vs-pilot KPI measurement: handover cycle time, summary cycle time, lesson
   retrieval speed, carry-forward integrity (PRD §22.1)

**Exit gate:** PRD §22.1 success-metric targets met (handover time -30%, lesson
retrieval -50%, summary effort -30%, ≥ 70% weekly active use, SME trust ≥ 80%).

### Phase 6+ — PRD Phase 2/3 capabilities
Cross-campaign repeated-pattern analytics, campaign onboarding packs, Learning Assist
microbriefs, multi-asset rollout. Out of scope for this plan version.

---

## 6. What this plan deliberately does NOT do

- **No autonomous operational recommendations.** PRD §19. The system drafts, humans
  approve.
- **No replacement for real-time rig data systems.** Live status feed is optional
  (PRD §18.1) and added only if a pilot operator provides one.
- **No physics simulators, no anti-collision/T&D packages, no ERP.** PRD §5.3.
- **No full LMS.** Learning Assist (PRD §23) is a Phase 6+ thin extension.
- **No Files API or context caching** (architecture decision retained from v3 — see
  `PLAN_v3_ingestion.md` rationale; see also v3-retrospective notes captured in this
  conversation history).

---

## 7. Open decisions for stakeholder review

1. **Graph DB choice**: Neo4j Community vs Apache AGE (Postgres graph extension) vs
   NetworkX-in-process vs managed (Neptune/Memgraph). Default is Neo4j Community per
   §5.4.1.i; final choice confirmed at Phase 2 kickoff, **not earlier**. Pilot scale
   does not need Neo4j until Phase 2 prerequisite gates (§5.4.1.b G-KG.1–G-KG.5) are
   green. Ordering is non-negotiable: clean extraction (L1) → entity validation &
   resolution (L2) → relationship tagging → KG load. See §5.4.1 for the full dossier.
2. **Postgres vs SQLite for pilot**: SQLite simpler for single-asset pilot; Postgres
   needed at multi-tenant. Plan: SQLite with SQLAlchemy now, Postgres-compatible schema
   so flip is one config change.
3. **Embedding model**: `gemini-embedding-exp-03-07` is current; revisit when
   `gemini-embedding-001` (GA) ships.
4. **JV / multi-operator partitioning** (PRD §24.3 open question). Assume single-operator
   pilot; defer multi-operator confidentiality model to Phase 3.
5. **Live data feeds** (PRD §24.3). Plan assumes document-first MVP per PRD §24.1.
6. **Confidence threshold for AI release** (PRD §24.3). Default `pass_confidence_threshold
   = 0.85` from v3 ingestion config; SME to validate during Phase 1 golden-set build.

---

## 8. Success Definition

This plan succeeds when, against a real pilot well report corpus:

- Any role can ask a question, get a **source-cited Markdown answer with at least one
  page-region image when relevant**, and click through to the exact page region
- A drafted shift handover preserves all critical carry-forward items with zero loss
  across a shift transition
- A drafted daily summary is approved by the operational owner with at most light edits
  in ≥ 80% of cases
- A planner can retrieve top-3 analog wells for a new section in under 1 minute and see
  the lessons that apply
- The system can correctly count NPT hours by IADC category across an entire campaign
  and tie each event back to its source artifact
- An incoming SME can audit every claim back to the rendered page region it came from

The user's two explicit asks — **99% accuracy** and **image grounding tied to source
docs** — are the gating criteria for every phase exit, not a "later" feature.
