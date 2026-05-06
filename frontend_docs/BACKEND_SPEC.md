# WellSynth AI — Backend Specification (Demo v1 Killer Features)

> **Version:** 1.0  
> **Date:** 2026-05-05  
> **Audience:** Backend engineers extending the existing FastAPI + Celery + Qdrant + Gemini stack.  
> **Scope:** The four demo-v1 features that go beyond plain `/chat`.  
> **Companion:** [`FRONTEND_SPEC.md`](FRONTEND_SPEC.md) §39 describes how the frontend consumes these endpoints.

---

## 0. North Star & Constraints

These four features turn WellSynth from "chat with PDFs" into a drilling-engineering tool. All four:

1. **Reuse existing infrastructure** — the same Qdrant collections, the same RAG tools (`search_chunks`, `search_entities`, `search_images`), the same Gemini chat models.
2. **Return structured JSON, not free-text blobs.** Every numeric or named entity in a response carries a `source_id` linking to a `provenance` record. This is the contract that lets the frontend render premium components instead of a markdown dump.
3. **Honor the repo guardrails.** No new dependencies, no new agents/config flags beyond what's listed here, no speculative features. We're adding **four LangGraphs** + four FastAPI routes, sharing the existing tool layer.
4. **Are deterministic-first.** Where rules can produce the answer (frequency counts, formation overlap, depth-band intersections), use rules. Use Gemini only for the parts that require language (one-line rationales, root-cause guesses from free-text DDR notes).

Hard rules:

- **No new auth layer.** Demo accepts requests as-is, same as `/chat`.
- **No new vector collections.** Reuse `wellsynthai_documents`, `wellsynthai_entities`, `wellsynthai_images`.
- **No new persistence.** Briefings and summaries are computed/cached to disk (`data/output/...`) and Redis. No Postgres, no migrations.
- **Provenance is non-negotiable.** Every response field that names a number or an entity must be traceable to `(doc_name, page_num, chunk_index|table_id|entity_canonical_id)`.

---

## 1. Feature → Endpoint Map

| # | Feature | Endpoint(s) | New module path |
|---|---------|-------------|-----------------|
| 1 | Pre-Spud Risk Briefing | `POST /briefings/pre-spud`, `GET /briefings`, `GET /briefings/{id}` | `app/agents/briefing/` |
| 2 | Lessons-in-Context | `POST /lessons/applicable` | `app/agents/lessons/` |
| 3 | Auto-Summary + Key Facts | `GET /documents/{job_id}/summary` (read) + `summarizer` task in postprocessor (write) | `app/pipeline/summarizer.py` |
| 4 | Cross-Document Compare | `POST /compare` | `app/agents/compare/` |

All four import from the existing `app.agents.rag.tools` (or `app.agents.rag_tools`) — they are **agents that share the RAG tool layer**, not new RAG agents.

---

## 2. Shared Building Blocks

### 2.1 Provenance shape (already in use by `/chat`)

```python
class Provenance(BaseModel):
    source_id: str                # "S1", "S2", ...
    doc_name: str
    page_num: int
    chunk_index: int | None = None
    table_id: str | None = None
    entity_canonical_id: str | None = None
    bbox: list[int] | None = None
    score: float
    kind: str                     # "text" | "table" | "entity" | "image"
```

Every endpoint below returns a `provenance: list[Provenance]` plus structured fields whose values reference `source_id` strings.

### 2.2 Shared retrieval helper

A new internal helper, `app/agents/_retrieval.py`, exposes:

```python
async def retrieve_for_facets(
    facets: list[str],
    *,
    job_id: str | None = None,
    doc_name: str | None = None,
    formations: list[str] | None = None,
    section: str | None = None,
    depth_band_md_m: tuple[float, float] | None = None,
    top_k: int = 8,
) -> RetrievalBundle
```

Returns a `RetrievalBundle` of `{chunks, entities, images}` with all three rerank-ready. Internally it just chains the existing tools — this is a pure convenience wrapper, not new search logic. **Do not reinvent retrieval.**

### 2.3 Citation collation

A pure function `collate_citations(items: list[Any]) -> tuple[list[Provenance], dict[str,str]]` returns a deduped provenance list plus a map from internal ID to `Sn` source IDs. Used identically across all four agents.

### 2.4 Domain primitives (lightweight; do **not** rebuild L2 yet)

`app/domain/intervals.py` — already partially there:

- `parse_depth(value, unit) -> meters_md`
- `bands_overlap(a, b) -> bool`
- `format_depth(m, unit) -> str`

`app/domain/iadc.py` — small map of common IADC NPT codes → human label (e.g., `"5.1": "Stuck pipe"`). 30 lines, hardcoded. **Not** the full L2 taxonomy — that is Phase 1B scope per `PLAN.md`.

---

## 3. Feature 1 — Pre-Spud Risk Briefing

### 3.1 Purpose

Given a planned-well descriptor (formations, section, depth band, field), produce a ranked list of risks observed in offset wells from the ingested corpus.

### 3.2 Endpoint contract

```
POST /briefings/pre-spud
```

**Request:**

```json
{
  "well_name": "ALPHA-12",
  "field": "Eagle Ford",
  "section": "production",
  "planned_formations": ["Austin Chalk", "Eagle Ford"],
  "md_band_m": [2400, 3500],
  "tvd_band_m": [2400, 3500],
  "operations": ["drilling", "casing"],
  "scope": {
    "job_ids": null,
    "doc_names": null
  }
}
```

**Response:**

```json
{
  "briefing_id": "uuid",
  "generated_at": "2026-05-05T12:34:00Z",
  "input": { ... echo of request ... },
  "corpus": {
    "offsets_considered": 14,
    "doc_names": ["ALPHA-09_EOWR", "ALPHA-07_DDR_2025-03-12", "..."]
  },
  "npt_risks": [
    {
      "rank": 1,
      "iadc_code": "5.1",
      "label": "Stuck pipe",
      "frequency": { "count": 6, "denominator": 14 },
      "duration_hours": { "p50": 28, "p90": 64, "total": 230 },
      "formation_context": ["Eagle Ford"],
      "depth_band_m": [3120, 3220],
      "root_causes": [
        { "text": "differential sticking", "source_ids": ["S3", "S7"] }
      ],
      "what_worked": [
        { "text": "spotting pill + reduced overbalance to 0.3 ppg", "source_ids": ["S3"] }
      ],
      "source_ids": ["S1","S2","S3","S7"]
    }
  ],
  "formation_hazards": [
    {
      "formation": "Austin Chalk",
      "hazard": "lost_circulation",
      "frequency": { "count": 3, "denominator": 14 },
      "depth_band_m": [2780, 2860],
      "source_ids": ["S4", "S5"]
    }
  ],
  "mitigations": [
    { "text": "Pre-spot LCM pill before Austin Chalk top", "source_ids": ["S4"] }
  ],
  "offset_wells": [
    {
      "doc_name": "ALPHA-09_EOWR",
      "field": "Eagle Ford",
      "td_md_m": 3380,
      "npt_total_h": 42,
      "spud_date": "2025-09-12"
    }
  ],
  "provenance": [
    {
      "source_id": "S1",
      "doc_name": "ALPHA-09_EOWR",
      "page_num": 18,
      "chunk_index": 4,
      "score": 0.91,
      "kind": "text"
    }
  ]
}
```

`GET /briefings` returns `{ briefings: [{ briefing_id, generated_at, well_name, field }] }` (history list, paginated).

`GET /briefings/{id}` returns the full briefing payload (cached on disk under `data/output/_briefings/{id}.json`).

### 3.3 LangGraph design

```
plan_corpus  ──► retrieve_offsets  ──► aggregate_npt  ──► aggregate_hazards
                                              │                   │
                                              └──── compose_mitigations (LLM) ◄─┘
                                                          │
                                                          └──► critic ──► finalize
```

**Nodes (in `app/agents/briefing/graph.py`):**

| Node | Type | Behaviour |
|---|---|---|
| `plan_corpus` | deterministic | Resolves the offset set: filter ingested docs by field name match (entity collection lookup), then by formation overlap, then by depth-band overlap. Falls back to vector search over `wellsynthai_documents` if entity match is empty. Caps at 25 offsets. |
| `retrieve_offsets` | deterministic + tools | For each offset doc, calls `search_entities(kind="npt")`, `search_chunks(query=f"{section} {','.join(formations)} risks")`, `search_images(query="log strip {formations[0]}")`. Bundles results. |
| `aggregate_npt` | deterministic | Groups NPT entities by `iadc_code`, aggregates `frequency`, `p50/p90/total` durations. Uses `app/domain/iadc.py` for human labels. Filters to NPT events whose recorded depth/formation overlaps the planned band. |
| `aggregate_hazards` | deterministic | Counts loss/overpressure/influx events per formation; computes depth-band envelopes. |
| `compose_mitigations` | **LLM** (one Gemini Flash call) | Given top 5 NPTs + their citations, asks the model to extract "what worked" and "root cause" lines verbatim or near-verbatim from the cited chunks. Prompt is tightly constrained — no synthesis allowed. |
| `critic` | **LLM** (one Gemini Flash call) | Verifies every numeric value in the draft appears in the provenance. Drops claims that don't. Same pattern as `/chat` synthesizer. |
| `finalize` | deterministic | Builds the `BriefingResult` Pydantic model, persists JSON to disk, writes Redis index entry, returns. |

### 3.4 LLM budget

Two Gemini calls per briefing: one Flash for `compose_mitigations`, one Flash for `critic`. Approx 4 k input + 1 k output tokens each → < $0.01 per briefing at current pricing.

### 3.5 Files to add

```
app/agents/briefing/
    __init__.py
    graph.py              # the LangGraph
    nodes.py              # individual node implementations
    schemas.py            # BriefingRequest, BriefingResult, RiskCard, HazardRow
    prompts.py            # compose_mitigations, critic prompts
app/routes/briefings.py   # FastAPI router
```

### 3.6 Persistence

- Per-briefing JSON: `data/output/_briefings/{briefing_id}.json`.
- Redis index: `briefings:index` (sorted set by `generated_at`), `briefings:{id}` (hash with summary fields for fast list rendering).
- TTL: 30 days; configurable via `settings.briefing_ttl_days`.

### 3.7 Edge cases

| Case | Behaviour |
|---|---|
| Zero offsets match field/formation | Return 200 with `corpus.offsets_considered = 0` and an `empty_state_reason` field. The frontend renders a clear empty state. |
| Single offset | Briefing still returns; ranking is degenerate but useful. |
| Unknown formation name | Best-effort vector match; flag in `warnings[]`. |
| Concurrent re-generation of same input | Idempotency: hash the request; if a briefing with same hash exists < 1 hour old, return cached. Fresh after 1 hour. |
| Depth band missing | Skip depth-band filter; rely on formation overlap only. |

### 3.8 Tests

- `tests/test_briefing_aggregation.py` — unit tests for NPT grouping, hazard counting, depth-band overlap.
- `tests/test_briefing_endpoint.py` — integration test against fixture corpus (3 mock offset docs in Qdrant).
- Golden test: a hand-crafted offset set with known counts — assert exact NPT frequency and citations.

---

## 4. Feature 2 — Lessons-in-Context

### 4.1 Purpose

Given a *current operational context* (a doc the user is on, a depth, a formation, an op type), surface the top N most-applicable lessons from the corpus, ranked by relevance × recency × tier weight.

### 4.2 Endpoint contract

```
POST /lessons/applicable
```

**Request:**

```json
{
  "context": {
    "formation": "Eagle Ford",
    "section": "production",
    "depth_md_m": 3180,
    "operation": "casing",
    "doc_name": "ALPHA-12_program",
    "job_id": null
  },
  "scope": { "job_ids": null, "doc_names": null },
  "top_k": 5
}
```

**Response:**

```json
{
  "lessons": [
    {
      "lesson_id": "L:ALPHA-09:p18:c2",
      "title": "Stuck pipe avoidance — Eagle Ford 9⅝ running",
      "situation": "Differential sticking observed at 3,180 m MD while running 9⅝ casing.",
      "outcome": "28 h NPT, recovered with spotting pill.",
      "fix": "Reduce overbalance to 0.3 ppg before 3,100 m and pre-spot pill.",
      "applicability": {
        "score": 0.92,
        "reasons": ["formation_match", "operation_match", "depth_band_match"]
      },
      "source_ids": ["S1"]
    }
  ],
  "provenance": [
    {
      "source_id": "S1",
      "doc_name": "ALPHA-09_EOWR",
      "page_num": 18,
      "chunk_index": 2,
      "score": 0.91,
      "kind": "text"
    }
  ]
}
```

### 4.3 Definition of a "lesson" (without an L2 lesson type)

We do not have a `Lesson` entity yet. For demo v1, a **lesson candidate** is any chunk that meets *all* of:

1. Comes from a chunk whose embedding is close (≥ 0.55) to the query *"lessons learned, what worked, mitigation, recommendation, do/don't, takeaway"* in the doc's domain language.
2. Lives on a page whose markdown contains any of: `lessons`, `learned`, `recommendation`, `mitigation`, `what worked`, `takeaway`, `best practice`. Header-aware — if the chunk is under a heading containing those words, that's stronger evidence.
3. Survives a Gemini Flash classifier pass (one call per top-N candidate) returning `is_lesson: bool, situation, outcome, fix`. Only candidates with `is_lesson=true` are returned.

This is **good enough for demo** without inventing a new entity type. Phase 1B's L2 work will replace step 1+2 with proper lesson entities; the endpoint's response shape is forward-compatible.

### 4.4 LangGraph design

```
build_query  ──►  retrieve_candidates  ──►  filter_heuristics  ──►  classify_lessons (LLM, batched)  ──►  rank  ──►  finalize
```

| Node | Type | Behaviour |
|---|---|---|
| `build_query` | deterministic | Composes query string from context: `"{operation} {formation} {section} lessons recommendations what worked"`. |
| `retrieve_candidates` | tools | `search_chunks` with the query, plus filter `kind != "table"`. Top 25. |
| `filter_heuristics` | deterministic | Drops chunks whose markdown doesn't contain any lesson-keyword and whose section heading doesn't either. Cap to 12. |
| `classify_lessons` | **LLM** (one batched Flash call, structured output) | Given up to 12 chunks, returns `[{idx, is_lesson, title, situation, outcome, fix}]`. |
| `rank` | deterministic | Score = retrieval_score × (0.5 + 0.5 * heuristic_boost) × tier_weight × recency_decay. Adds `applicability.reasons` based on which context fields matched chunk metadata (formation, section, depth band). |
| `finalize` | deterministic | Top `top_k`; collate citations. |

### 4.5 LLM budget

One batched Gemini Flash call per request. ~3 k input / 800 output tokens. ~$0.003 per call.

### 4.6 Files to add

```
app/agents/lessons/
    __init__.py
    graph.py
    nodes.py
    schemas.py
    prompts.py            # the classify-lessons structured prompt
app/routes/lessons.py
```

### 4.7 Edge cases

- Empty context → returns 422.
- No candidates → returns `{ lessons: [], provenance: [] }` with `empty_state_reason: "no_lesson_keywords_in_corpus"`.
- All candidates classified `is_lesson=false` → same as above with `"no_classified_lessons"`.

### 4.8 Tests

- Golden: 3 hand-crafted chunks, known ground truth for `is_lesson` after the classifier prompt.
- Heuristic test: keyword-and-heading filter is deterministic and unit-testable.

---

## 5. Feature 3 — Document Auto-Summary + Key Facts

### 5.1 Purpose

At the end of ingestion, produce a 3-paragraph human summary plus a structured `key_facts` block. Persist to the document's manifest. Render everywhere a doc is displayed.

### 5.2 Where it runs

This is **not a new agent**. It is a final step in the ingestion postprocessor.

`app/pipeline/summarizer.py` is added and called from the existing `app/pipeline/postprocessor.py` after `full_document.md` is written and entities are indexed.

### 5.3 Endpoint contract (read-only)

```
GET /documents/{job_id}/summary
```

**Response:**

```json
{
  "job_id": "uuid",
  "doc_name": "ALPHA-12_EOWR",
  "summary": "Three paragraphs of markdown ...",
  "key_facts": {
    "td_md_m":            { "value": 3380, "unit": "m", "source_id": "S1" },
    "td_tvd_m":           { "value": 3320, "unit": "m", "source_id": "S1" },
    "spud_date":          { "value": "2025-09-12", "source_id": "S2" },
    "completion_date":    { "value": "2025-11-02", "source_id": "S2" },
    "formations":         { "value": ["Austin Chalk", "Eagle Ford", "Buda"], "source_ids": ["S3", "S4"] },
    "casing_program":     { "value": "13⅜ → 9⅝ → 7", "source_id": "S5" },
    "mud_system":         { "value": "OBM 9.6 ppg", "source_id": "S6" },
    "npt": {
      "total_h": 42,
      "top_categories": [
        { "iadc_code": "5.1", "label": "Stuck pipe", "hours": 28 },
        { "iadc_code": "8.1", "label": "Lost circulation", "hours": 14 }
      ],
      "source_ids": ["S7", "S8"]
    }
  },
  "provenance": [ /* same shape */ ]
}
```

Any field that could not be confidently extracted is **omitted** (never `null`-faked). The frontend's `KeyFactsPanel` renders `—` for missing facts.

### 5.4 Computation

Two steps, both deterministic-first:

**Step A — Deterministic mining** (no LLM):
- Pull all entities of types `depth`, `formation_top`, `date`, `casing_size`, `mud_weight`, `npt_event` from the entity store for this `job_id`.
- TD = max canonical depth value tagged "TD" or "total depth" (entity payload already records this where extraction tagged it).
- Spud / completion = earliest / latest dates tagged "spud" / "completion."
- Casing program = sorted distinct casing sizes by depth.
- Mud system = the most-cited fluid system entity.
- NPT total / top categories = sum + group-by IADC code.

**Step B — LLM summary** (one Gemini Flash call):
- Input: the `key_facts` from Step A + the first chunk of `full_document.md` (truncated to 12 k tokens) + any executive-summary section detected by header.
- Output: 3 paragraphs of markdown, each ≤ 80 words, written in the brand voice (precise, terse, neutral — see `FRONTEND_SPEC.md` §35).
- Constrained: no numbers introduced that aren't in `key_facts`. Critic re-pass identical to `/chat` synthesizer.

If Step A produces nothing useful (very sparse doc), fall back to a pure markdown snippet of the first 250 words of `full_document.md` — better something than nothing.

### 5.5 Persistence

- Written to `data/output/{doc_dir}/summary.json`.
- Mirrored in the existing job manifest under `manifest["summary"]` and `manifest["key_facts"]` so a single Redis read serves the dashboard / library.

### 5.6 Files to add

```
app/pipeline/summarizer.py        # the function, called from postprocessor
app/routes/documents.py           # GET /documents/{job_id}/summary
                                  # (NEW route module, not extending /ingest)
```

### 5.7 Tests

- `tests/test_summarizer.py` — unit tests for Step A on a fixture entity set.
- Golden: one full ingestion run end-to-end on the bundled `GSWA_Harvey_1_WCR.pdf` asserts a non-empty summary, ≥ 3 key facts populated.

### 5.8 Backfill

Add a small CLI: `python -m app.tools.backfill_summaries` that iterates all completed jobs and writes summaries for any job missing one. **Not** an HTTP endpoint — admin-only.

---

## 6. Feature 4 — Cross-Document Compare

### 6.1 Purpose

Given two `job_id`s, return a structured comparison: comparable rows, deltas, common entities, and entities exclusive to each side. The frontend renders it; this endpoint does not return prose.

### 6.2 Endpoint contract

```
POST /compare
```

**Request:**

```json
{
  "job_a": "uuid",
  "job_b": "uuid",
  "facets": ["td", "spud_date", "formations", "casing_program", "mud_system", "npt"]
}
```

(`facets` defaults to all of the above.)

**Response:**

```json
{
  "job_a": "uuid", "doc_a": "ALPHA-12_EOWR",
  "job_b": "uuid", "doc_b": "ALPHA-09_EOWR",
  "rows": [
    {
      "label": "Total depth (MD)",
      "facet": "td_md_m",
      "a": { "value": 3380, "unit": "m", "source_id": "A1" },
      "b": { "value": 3420, "unit": "m", "source_id": "B1" },
      "delta": { "value": 40, "unit": "m", "direction": "increase", "severity": "info" }
    },
    {
      "label": "Top NPT cause",
      "facet": "npt_top_cause",
      "a": { "value": "Stuck pipe", "source_id": "A4" },
      "b": { "value": "Lost circulation", "source_id": "B4" },
      "delta": { "direction": "differs", "severity": "warn" }
    }
  ],
  "common_formations": ["Eagle Ford", "Austin Chalk", "Buda", "..."],
  "only_in_a": { "formations": ["Edwards"] },
  "only_in_b": { "formations": ["Wolfcamp", "Bone Spring"] },
  "callouts": [
    {
      "kind": "npt_increase",
      "text": "NPT total increased by 36 h (42 h → 78 h).",
      "severity": "bad"
    }
  ],
  "provenance": [ ... ]
}
```

### 6.3 Computation

100% deterministic. **No LLM call.** Re-uses the `key_facts` from Feature 3 (Auto-Summary).

| Facet | Computation |
|---|---|
| `td_md_m` / `td_tvd_m` | `key_facts.td_*` — direct comparison + delta |
| `spud_date` | parse → `dateutil` → days delta |
| `formations` | set algebra: common, only_a, only_b |
| `casing_program` | string compare; "match" if identical, "differs" otherwise |
| `mud_system` | string compare + ppg delta if both have a numeric weight |
| `npt_total_h` | numeric delta + severity rule (≥ 24 h delta → warn; ≥ 72 h → bad) |
| `npt_top_cause` | string compare |

`callouts[]` are generated by simple rules over computed deltas — no model.

### 6.4 Why deterministic

The user expectation for a compare view is **exact, repeatable, defensible** numbers. Any LLM call here introduces non-determinism that erodes trust. If we want a one-line narrative *describing* the diff, that goes through the existing `/chat` endpoint with both `job_id`s in scope — a separate concern from the structured diff.

### 6.5 Files to add

```
app/agents/compare/
    __init__.py
    diff.py               # the deterministic diff logic
    schemas.py            # CompareRequest, CompareResult, CompareRow
app/routes/compare.py
```

### 6.6 Edge cases

| Case | Behaviour |
|---|---|
| Either job missing summary | Lazily compute it on the fly via the summarizer (slower path), then return. |
| Either job not yet `completed` | Return 409 with clear `detail`. |
| Same job_id on both sides | Return 422 — "compare requires two distinct documents." |
| Facet not extractable on either side | Row omitted from `rows[]`, listed in `unavailable[]`. |

### 6.7 Tests

- Pure unit tests on the diff module against synthetic `key_facts` payloads.
- Integration: ingest two fixture PDFs (or use cached summaries) and assert exact compare output.

---

## 7. Cross-Cutting Concerns

### 7.1 Routing & app wiring

`app/main.py` registers four new routers:

```python
from app.routes import briefings, lessons, documents as docs_route, compare
app.include_router(briefings.router)
app.include_router(lessons.router)
app.include_router(docs_route.router)   # /documents/{job_id}/summary
app.include_router(compare.router)
```

### 7.2 Response model parity

Every endpoint exposes a Pydantic response model and is documented in OpenAPI. The frontend types in `lib/types.ts` are generated (or manually mirrored) from these models — single source of truth.

### 7.3 Caching

| Endpoint | Cache key | TTL |
|---|---|---|
| `POST /briefings/pre-spud` | `sha256(canonicalized_request)` | 1 h (re-uses if same input within window) |
| `POST /lessons/applicable` | `sha256(context + scope)` | 10 min |
| `GET /documents/{id}/summary` | `id` | infinite (recomputed only by backfill or new ingestion) |
| `POST /compare` | `sha256(job_a, job_b, facets)` | 1 h |

All cached payloads live in Redis with a `cache:{endpoint}:{hash}` key.

### 7.4 Rate limiting

Each LLM-using endpoint shares the existing per-process Gemini semaphore. No separate limit; concurrent demo load is well below the cap.

### 7.5 Telemetry & logging

Every endpoint logs (structlog JSON):
- `endpoint` name
- `request_hash`
- `cache_hit: bool`
- `latency_ms`
- `tools_called: list[str]`
- `llm_calls: int`
- `tokens: { in, out }`
- `result_status: ok | empty | partial`

These feed straight into the existing logging pipeline. No new infra.

### 7.6 Error contract

| HTTP | When |
|---|---|
| `200` | Success, including "empty result" with `empty_state_reason` |
| `404` | Briefing id not found; job id not ingested |
| `409` | Compare requested while a job is not yet `completed` |
| `422` | Validation failure on request body |
| `500` | LLM or tool error (logged, generic message returned) |
| `503` | Gemini key not configured |

Same patterns as the existing `/chat` and `/ingest` routes.

---

## 8. Files Manifest (everything new, in one place)

```
app/
├── agents/
│   ├── briefing/
│   │   ├── __init__.py
│   │   ├── graph.py
│   │   ├── nodes.py
│   │   ├── prompts.py
│   │   └── schemas.py
│   ├── lessons/
│   │   ├── __init__.py
│   │   ├── graph.py
│   │   ├── nodes.py
│   │   ├── prompts.py
│   │   └── schemas.py
│   ├── compare/
│   │   ├── __init__.py
│   │   ├── diff.py
│   │   └── schemas.py
│   └── _retrieval.py                 # shared helper
├── domain/
│   ├── intervals.py                  # extended
│   └── iadc.py                       # NEW (small)
├── pipeline/
│   ├── postprocessor.py              # MODIFIED: calls summarizer
│   └── summarizer.py                 # NEW
├── routes/
│   ├── briefings.py                  # NEW
│   ├── lessons.py                    # NEW
│   ├── documents.py                  # NEW (only /documents/{id}/summary)
│   └── compare.py                    # NEW
├── tools/
│   └── backfill_summaries.py         # NEW (admin CLI)
└── main.py                           # MODIFIED: include_router x4

tests/
├── test_briefing_aggregation.py
├── test_briefing_endpoint.py
├── test_lessons_classifier.py
├── test_lessons_endpoint.py
├── test_summarizer.py
├── test_compare_diff.py
└── fixtures/
    └── briefing_corpus/              # mock offset docs
```

No changes to `requirements.txt`. No new services. No DB migrations.

---

## 9. Build Order (one PR per feature, four PRs total)

1. **PR-1 — Auto-Summary + Key Facts.** Lowest risk; pure additive. Unblocks the demo by populating the dashboard, library, and Overview tab. Backfill existing jobs.
2. **PR-2 — Compare.** Builds directly on PR-1's `key_facts`. 100% deterministic, fast to land.
3. **PR-3 — Pre-Spud Briefing.** First LLM-using new agent. Reuses retrieval helper.
4. **PR-4 — Lessons-in-Context.** Second LLM-using new agent. Reuses the same patterns; smallest scope.

Each PR ships with tests + an OpenAPI snapshot so the frontend can mock against it before the backend even merges.

---

## 10. Acceptance Criteria (Demo Sign-Off)

| Feature | Demo passes when... |
|---|---|
| Auto-Summary | Library card shows 3 facts; Overview tab renders 3-paragraph summary + 6 key facts; missing facts render as `—`. |
| Compare | `/compare` returns deterministic rows + deltas + callouts on two real ingested docs in < 500 ms (cached < 50 ms). Frontend compare screen renders without any client-side fallback. |
| Pre-Spud Briefing | A 3-min walkthrough: form → "Generate" → premium report renders with ≥ 3 NPT risks, ≥ 1 hazard, ≥ 1 mitigation, all citations clickable to page. PDF export works. |
| Lessons-in-Context | Opening any document workspace shows the right-rail widget with 3 contextual lessons within 2 s. Each lesson cites a real page. |

---

*End of Backend Specification — Demo v1 Killer Features.*
