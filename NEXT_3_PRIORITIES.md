# WellSynth AI — Next 3 Build Priorities

> Derived from PLAN.md v4, current codebase state, and dependency ordering.
> Each priority lists what to build, why it's next, what it unblocks, and exit criteria.

---

## Priority 1 — L1 Extraction Hardening (Accuracy A3–A6 + Gaps G-L1.3–G-L1.6)

### Why first

The extraction pipeline is the foundation. Every downstream layer (RAG, KG, agents)
can only be as good as what L1 extracts. Today, the pipeline extracts markdown and
tables but does **not** output structured entities, does not adapt DPI for hard pages,
and has no numeric guardrails. Fixing these before building L2 means L2 has clean
inputs from day one.

### What to build

| # | Item | New / Modified file | Effort |
|---|---|---|---|
| 1.1 | **Structured entity extraction** (G-L1.3) — Add `entities` field to extractor responseSchema: well IDs, depths (value+unit+type), formation tops, NPT events, fluids, equipment, dates | `app/agents/prompts.py`, `app/agents/extractor.py`, `app/models/schemas.py` | Medium |
| 1.2 | **Persist page PNGs** (G-L1.1) — Save rendered page PNGs to `data/pages/{doc_id}/page_{n}.png` instead of discarding after extraction. Required for image grounding | `app/pipeline/orchestrator.py`, `app/storage/file_storage.py` | Small |
| 1.3 | **Bounding boxes for tables & images** (G-L1.2) — Extend extractor schema to return `[ymin, xmin, ymax, xmax]` normalized bboxes for every table and described image | `app/agents/prompts.py`, `app/agents/extractor.py` | Small |
| 1.4 | **Adaptive DPI** (G-L1.4, accuracy A3) — Scanned pages or high-density pages render at 300 DPI; sparse pages at 150 DPI. Cuts cost ~40% on easy pages | `app/pipeline/page_streamer.py`, `app/pipeline/preprocessing.py` | Small |
| 1.5 | **ROI second-look** (G-L1.5, accuracy A3) — On validator gap, crop the specific bbox region at 300 DPI and re-extract just that region in iter-2 | `app/agents/extractor.py`, `app/agents/validator.py`, `app/pipeline/orchestrator.py` | Medium |
| 1.6 | **Numeric guardrails** (G-L1.6, accuracy A5) — Every depth/pressure/weight/volume value checked against PyMuPDF baseline text + physical range sanity. Discrepancy → escalate to Pro | `app/agents/validator.py` (new guardrail pass), `app/models/schemas.py` | Medium |
| 1.7 | **Cross-page reconciliation** (accuracy A6) — Validate that depth sequences are monotonic, formation top depths are consistent across pages, table column shapes align | New: `app/pipeline/reconciler.py` | Medium |

### Dependencies

- None — this builds on the existing L1 pipeline.

### What it unblocks

- L2 entity resolution (needs structured entity output from 1.1)
- Image grounding in L5 (needs persisted PNGs from 1.2 + bboxes from 1.3)
- Production-quality ingestion (numeric guardrails from 1.6 catch hallucinated numbers)

### Exit criteria

- [ ] Extractor returns structured `entities[]` with typed fields for every page
- [ ] Page PNGs persisted to disk and accessible via API
- [ ] Bboxes returned for all tables and described images
- [ ] Scanned/dense pages render at 300 DPI; sparse pages at 150 DPI
- [ ] Validator gaps trigger ROI crop re-extraction before escalation
- [ ] Numeric values cross-checked against baseline; discrepancies flagged
- [ ] Depth monotonicity and formation consistency validated cross-page
- [ ] End-to-end test: ingest a real 20+ page DDR, all pages succeed, entities extracted

---

## Priority 2 — L2 Domain Knowledge Layer

### Why second

L2 is the **single biggest accuracy lever after L1**. It constrains the model's output
space at generation time (via `responseSchema` enums) rather than cleaning up free text
afterward. Without it, the same NPT event appears as "stuck pipe", "Stuck Pipe", "SP"
— three different strings that defeat aggregation, KG construction, and cross-well
comparison.

### What to build

| # | Item | New file | Effort |
|---|---|---|---|
| 2.1 | **Well taxonomy module** — Python enums + JSON schemas for: NPT categories (IADC 26-code), operation types, depth types (MD/TVD/KB/RT/MSL/AHBDF), section types, artifact classes | New: `app/domain/well_taxonomy.py` | Medium |
| 2.2 | **Unit normalization** — Bidirectional converters: ft↔m, ppg↔kg/m³, bbl↔m³, psi↔kPa, in↔mm, °F↔°C. Store canonical unit per field, convert on ingest | New: `app/domain/units.py` | Small |
| 2.3 | **Formation alias registry** — Map regional naming variants to canonical formation names (config-driven per asset) | New: `app/domain/formations.py` or extend `well_taxonomy.py` | Small |
| 2.4 | **Entity resolver** — Embed each new entity → top-K against canonical store → merge if sim > 0.92 AND fuzzy > 0.85 AND domain rules agree → else create new canonical ID with provenance | New: `app/domain/entity_resolver.py` | Large |
| 2.5 | **Wire L2 into L1 extraction** — Export enums from 2.1 into the extractor's `responseSchema` so Gemini picks from closed lists instead of free text. Update prompts | `app/agents/prompts.py`, `app/agents/extractor.py` | Medium |
| 2.6 | **Wire resolver into post-processing** — After extraction, run entity resolver on all entities. Assign canonical IDs. Persist `entities_resolved.json` per document | `app/pipeline/postprocessor.py` or `orchestrator.py` | Medium |

### Dependencies

- Priority 1 item 1.1 (structured entity extraction) must be done first — resolver needs typed entities as input.

### What it unblocks

- **Knowledge Graph (L5.4)** — gates G-KG.2 (unit/NPT conformance = 100%) and G-KG.3 (canonical ID collision ≤ 1%)
- **Accuracy A4** (schema-constrained extraction) and **A7** (entity resolution)
- **Cross-well comparison** — normalized entities allow aggregation across documents
- **L5 `entities_facts` collection** — one Qdrant point per resolved entity with canonical ID

### Exit criteria

- [ ] `app/domain/well_taxonomy.py` exports enums for NPT, operations, depths, sections, artifacts
- [ ] Unit conversion functions cover all 6 pairs with round-trip accuracy
- [ ] Formation alias registry loaded from config, returns canonical name for any variant
- [ ] Entity resolver produces canonical IDs; collision rate ≤ 1% on dev corpus
- [ ] Extractor responseSchema uses L2 enums — Gemini forced to pick from closed lists
- [ ] Post-processing assigns canonical IDs to all entities in output
- [ ] Test: ingest two documents mentioning the same well/formation with different naming → resolver merges to single canonical ID

---

## Priority 3 — L5 Retrieval Completion (Entities + Images + Hybrid Pipeline)

### Why third

Basic RAG works (text chunks → Qdrant → search/chat). But the plan defines **three
Qdrant collections** and a **hybrid retrieval pipeline** with tier-weighted reranking.
The missing pieces are what make the system actually useful for well engineering:
filtered entity queries ("all NPT > 100h in Eagle Ford"), image grounding, and
evidence-tier ranking.

### What to build

| # | Item | New / Modified file | Effort |
|---|---|---|---|
| 3.1 | **`entities_facts` Qdrant collection** — One point per resolved L2 entity. Embedding of canonical text form. Payload: structured fields + provenance + canonical_id. Indexed on well_api, formation, npt_category, depth_range, event_date | `app/vectorstore/qdrant_store.py`, new: `app/vectorstore/entity_indexer.py` | Medium |
| 3.2 | **Entity upsert in pipeline** — After L2 resolution, embed and upsert all entities into `entities_facts`. Wire into Phase 4 of orchestrator | `app/pipeline/orchestrator.py` | Small |
| 3.3 | **`image_descriptions` Qdrant collection** — One point per described image/figure/log strip from L1. Payload: doc_id, page_num, bbox, description, image_type, tier_weight | `app/vectorstore/qdrant_store.py`, new: `app/vectorstore/image_indexer.py` | Medium |
| 3.4 | **Image crop service** — Given (doc_id, page_num, bbox), crop the stored page PNG and return the image. Wire into search/chat responses | New: `app/services/image_cropper.py`, update `app/routes/search.py` | Small |
| 3.5 | **Query intent classifier** — Route queries to the right index: factual → chunks_text, entity/aggregation → entities_facts, image → image_descriptions | New: `app/retrieval/query_router.py` | Medium |
| 3.6 | **Tier-weighted reranking** — Multiply semantic score by document tier weight (§3.3: approved program=1.0, DDR=0.85, draft=0.5, etc.) + recency boost | New: `app/retrieval/reranker.py` | Small |
| 3.7 | **Hybrid retrieval pipeline** — Orchestrate: classify intent → route to index(es) → collect results → rerank → compose cited answer with provenance | New: `app/retrieval/hybrid_pipeline.py`, update `app/agents/rag_agent.py` | Large |
| 3.8 | **Image grounding in chat** — When retrieval hits an image-type chunk, include the cropped page region in the response alongside the text answer | `app/agents/rag_agent.py`, `app/routes/chat.py` | Medium |

### Dependencies

- Priority 1 items 1.1–1.3 (entities, PNGs, bboxes) — raw material for entity and image indexing
- Priority 2 items 2.4–2.6 (resolved entities with canonical IDs) — entities_facts needs canonical IDs

### What it unblocks

- **Source-grounded citations** with evidence cards (PRD §13.2 FR-010)
- **Image grounding** — cropped diagrams/logs returned with answers
- **Filtered entity queries** — "show all stuck pipe events deeper than 3000m" without LLM
- **L6 synthesis agents** — daily summary, handover, program QA all consume the hybrid pipeline
- **Knowledge Graph (future)** — hybrid pipeline is the integration point for graph queries

### Exit criteria

- [ ] `entities_facts` collection created with proper indices; entities upserted after ingestion
- [ ] `image_descriptions` collection created; image descriptions indexed with bboxes
- [ ] Image crop API returns PNG for any (doc_id, page_num, bbox)
- [ ] Query router correctly classifies factual vs entity vs image queries
- [ ] Reranker applies tier weights; approved-program results rank above draft results at equal similarity
- [ ] Hybrid pipeline returns results from multiple collections when appropriate
- [ ] Chat response includes cropped image when query relates to a figure/diagram
- [ ] Test: "What was the stuck pipe depth in Well ABC-1?" returns the entity with provenance + page crop

---

## Dependency Graph

```
Priority 1: L1 Hardening
  1.1 Structured entities ──────┐
  1.2 Persist PNGs ─────────────┤
  1.3 Bboxes ───────────────────┤
  1.4 Adaptive DPI              │
  1.5 ROI second-look           │
  1.6 Numeric guardrails        │
  1.7 Cross-page reconciliation │
                                │
Priority 2: L2 Domain Knowledge │
  2.1 Taxonomy enums ◄──────────┘ (uses entity output from 1.1)
  2.2 Unit normalization
  2.3 Formation aliases
  2.4 Entity resolver ─────────┐
  2.5 Wire into extraction     │
  2.6 Wire into post-proc ─────┤
                               │
Priority 3: L5 Retrieval       │
  3.1 entities_facts ◄─────────┘ (needs canonical IDs from 2.4-2.6)
  3.2 Entity upsert ◄──────────── (needs entities_facts collection)
  3.3 image_descriptions ◄─────── (needs PNGs from 1.2 + bboxes from 1.3)
  3.4 Image crop service ◄─────── (needs persisted PNGs from 1.2)
  3.5 Query router
  3.6 Tier-weighted reranker
  3.7 Hybrid pipeline
  3.8 Image grounding in chat
```

## Suggested Build Order (sequential within each priority, priorities can overlap)

1. **Start with 1.1 + 1.2 + 1.3** (entity output, PNGs, bboxes) — these are the foundation
2. **Then 1.4 + 1.5 + 1.6** (DPI, ROI, guardrails) — accuracy hardening
3. **Then 2.1 + 2.2 + 2.3** (taxonomy, units, formations) — domain vocabulary
4. **Then 1.7** (cross-page reconciliation) — needs entity structure from 1.1
5. **Then 2.4 + 2.5 + 2.6** (resolver, wire into pipeline) — entity resolution
6. **Then 3.1 + 3.2 + 3.3 + 3.4** (new Qdrant collections, image crop) — indexing
7. **Then 3.5 + 3.6 + 3.7 + 3.8** (router, reranker, hybrid pipeline, image grounding) — retrieval

---

## What's NOT in these 3 priorities (and why)

| Layer | Why deferred |
|---|---|
| L3 Tenant & Hierarchy | No multi-tenant need until real users; schema can be added later without rework |
| L4 Artifact Versioning | Single-version upload is sufficient for pilot; revisioning adds complexity with no current user |
| L5.4 Knowledge Graph (Neo4j) | Explicitly gated behind L2 exit criteria (G-KG.1–G-KG.6). Build entities first, graph later |
| L6 Specialized Agents | Daily summary, handover, program QA all consume the hybrid pipeline — build that first |
| L7 RBAC | No multi-user access control needed for pilot |
| L8 Workspace UI | API-first; UI is a separate frontend effort after backend stabilizes |
| A8 SME Review Queue | Needs a UI; defer to post-pilot |
| A9 Golden-set CI | Needs hand-labeled pages; start collecting now, formalize after A3–A6 are built |
