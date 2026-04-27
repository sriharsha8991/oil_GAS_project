# Wellsynthai Ingestion Pipeline — Full Implementation Plan (v3)

> **v3 changes**: Reverted to per-page rasterization (no Files API, no caching), introduced
> a streaming page-by-page pipeline with 100 concurrent workers, and removed batching to
> maximise per-page accuracy on dense well reports. See "What Changed from v2" section at
> the bottom for the rationale.

## Architecture Overview

```
PDF Input
    │
    ▼
┌──────────────────────────────────────┐
│  FastAPI Service                     │
│  POST /ingest                        │
│  GET  /ingest/{job_id}               │
│  GET  /health                        │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  Phase 0: Baseline Text (local)      │
│  pymupdf4llm.to_markdown per page    │
│  → PageBaseline (text, is_scanned)   │
│  Used by validator as 2nd opinion    │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│  Phase 1: Streaming Page Rasterizer (PRODUCER)               │
│  page.get_pixmap(dpi=200).tobytes("png")                     │
│  Push PageImage(page_num, png_bytes) to asyncio.Queue        │
│  ─ does NOT wait for all pages to render before pushing ─    │
└──────────┬───────────────────────────────────────────────────┘
           │ (deque fills as pages render)
           ▼
┌──────────────────────────────────────────────────────────────┐
│  Phase 2: Per-Page Extraction Workers (100 CONCURRENT)       │
│  Singleton GeminiClient + asyncio.Semaphore(100)             │
│  Inline PNG bytes via types.Part.from_bytes(mime=image/png)  │
│  ─ NO batching, one page = one worker ─                      │
│                                                              │
│  per page:  ┌─────────────┐    ┌──────────────┐             │
│             │ Extractor    │──▶│ Validator    │             │
│             │ Flash 2.5    │   │ Flash 2.5    │             │
│             └──────▲──────┘    └──────┬───────┘             │
│                    │ iter < 3 & gaps  │                     │
│                    └──────────────────┘                     │
│                                       │ iter == 3 & gaps    │
│                            ┌──────────▼───────┐             │
│                            │ Escalator         │             │
│                            │ Pro 2.5 (FINAL)   │             │
│                            └──────────────────┘             │
└──────────────────────┬───────────────────────────────────────┘
                       │ (per-page results collected by page_num)
                       ▼
┌──────────────────────────────────────┐
│  Phase 3: Post-Processing            │
│  Per-page markdown assembly          │
│  Cross-page table merging            │
│  Header/footer dedup                 │
│  Full document markdown + table JSON │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  Phase 4: Heading-Aware Chunking     │
│  Split on markdown headings          │
│  Attach table JSON to chunk metadata │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  Phase 5: Qdrant Ingestion           │
│  Gemini embeddings                   │
│  Self-hosted Docker                  │
└──────────────────────────────────────┘

Performance target: 100 pages → < 30s end-to-end on Tier 3 Gemini
```

---

## Tech Stack

| Component            | Technology                                              |
|----------------------|---------------------------------------------------------|
| Language             | Python 3.11+                                            |
| Orchestration        | LangGraph (per-page state machine)                      |
| LLM (iter1, iter2)   | Gemini 2.5 Flash                                        |
| LLM (iter3 only)     | Gemini 2.5 Pro                                          |
| Embeddings           | gemini-embedding model                                  |
| PDF → Image          | PyMuPDF (fitz) page.get_pixmap() → PNG bytes (inline)   |
| PDF baseline text    | PyMuPDF (fitz) — used as validator reference            |
| OCR Fallback         | None in v3 — Gemini vision handles scanned pages         |
| Vector DB            | Qdrant (self-hosted Docker)                             |
| API Framework        | FastAPI + Uvicorn                                       |
| Async Runtime        | asyncio + Semaphore (100 concurrent on Tier 3)          |
| Page Queue           | asyncio.Queue (streaming producer→consumer)             |
| Logging/Tracing      | structlog + per-page trace context                      |
| Chunking             | langchain MarkdownTextSplitter (heading-aware)          |

---

## Project Structure

```
wellsynthai/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI app entry point
│   ├── config.py                  # Settings (Gemini key, Qdrant URL, DPI, concurrency)
│   ├── models/
│   │   ├── __init__.py
│   │   ├── schemas.py             # Pydantic models (PageBaseline, PageImage, PageExtraction)
│   │   └── state.py               # LangGraph PageState TypedDict
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── orchestrator.py        # Job lifecycle + producer/consumer wiring
│   │   ├── preprocessing.py       # Phase 0: PyMuPDF baseline text
│   │   ├── page_streamer.py       # Phase 1: rasterize → PageDeque (streaming producer)
│   │   └── postprocessor.py       # Phase 3: per-page MD, cross-page table merge, full doc
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── graph.py               # LangGraph wiring + worker pool
│   │   ├── extractor.py           # Extractor node (Flash for iter1/2)
│   │   ├── validator.py           # Validator node (Flash)
│   │   ├── escalation.py          # Escalator node (Pro for iter3)
│   │   └── prompts.py             # All system prompts and prompt builders
│   ├── vectorstore/
│   │   ├── __init__.py
│   │   ├── embeddings.py          # Gemini embedding wrapper
│   │   ├── chunker.py             # Heading-aware MD chunking + table JSON metadata
│   │   └── qdrant_client.py       # Qdrant collection + upsert
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── logging.py             # structlog setup with per-page trace context
│   │   └── gemini_client.py       # Singleton Gemini client + Semaphore(100)
│   └── storage/
│       ├── __init__.py
│       └── file_storage.py        # Markdown + table JSON output
├── tests/
│   ├── __init__.py
│   ├── test_preprocessing.py
│   ├── test_page_streamer.py
│   ├── test_agents.py
│   ├── test_postprocessor.py
│   ├── test_chunking.py
│   └── test_pipeline_e2e.py
├── docker-compose.yml             # Qdrant + app services
├── Dockerfile
├── pyproject.toml
├── .env.example
└── README.md
```

**Files removed in v3** (no longer needed):
- `app/pipeline/structure_scan.py` — no Phase 1 doc-structure scan
- `app/pipeline/batch_queue.py` — no batching
- `app/utils/pdf_uploader.py` — no Files API uploads
- `app/utils/pdf_splitter.py` — no 1000-page Files API limit, so no splitting needed

---

## Detailed Phase Breakdown

### Phase 0: Baseline Text Extraction (Local, No LLM)

**File**: `app/pipeline/preprocessing.py`

**Purpose**: Produce a cheap, fast baseline text per page that the Validator can use to cross-check Gemini's extraction. Gemini vision is the primary source; PyMuPDF text is the **independent second opinion**.

1. Open PDF with PyMuPDF (`fitz`)
2. For each page:
   - Extract text with `pymupdf4llm.to_markdown(..., page_chunks=True)` — preserves layout
   - Detect scanned: if `len(text.strip()) < 50` chars, mark `is_scanned=True`
3. Store per-page `PageBaseline` — used by the Validator in Phase 2 as reference text
4. No OCR fallback in v3 — Gemini's vision handles scanned pages natively from the rendered PNG

**Why no PaddleOCR**: When a page is scanned, the rendered PNG IS the source of truth for Gemini. Adding PaddleOCR would just be a third-party noise source. The validator can detect scanned pages from `is_scanned=True` and lower the bar accordingly.

---

### Phase 1: Streaming Page Rasterizer

**File**: `app/pipeline/page_streamer.py`

**Purpose**: Render each PDF page to a PNG image and push to a deque the instant it's ready. Workers in Phase 2 consume from the deque concurrently — they do NOT wait for all pages to finish rendering.

```python
import asyncio
import fitz  # PyMuPDF
from dataclasses import dataclass

@dataclass
class PageImage:
    page_num: int            # 0-indexed
    png_bytes: bytes         # rendered PNG
    width: int
    height: int
    dpi: int
    is_scanned: bool         # from Phase 0
    baseline_text: str       # from Phase 0

class PageDeque:
    """Producer-consumer queue. Producer (rasterizer) pushes;
    100 consumers (extraction workers) pop concurrently."""
    def __init__(self) -> None:
        self._queue: asyncio.Queue[PageImage | None] = asyncio.Queue()

    async def push(self, page: PageImage) -> None:
        await self._queue.put(page)

    async def pop(self) -> PageImage | None:
        return await self._queue.get()

    def close(self, num_consumers: int) -> None:
        """Send sentinel for each consumer so they exit cleanly."""
        for _ in range(num_consumers):
            self._queue.put_nowait(None)
```

**Rasterizer** (single producer task):

```python
async def rasterize_pages(pdf_path: Path, baselines: list[PageBaseline],
                         deque: PageDeque, dpi: int = 200) -> None:
    doc = fitz.open(pdf_path)
    zoom = dpi / 72  # PDF default is 72 DPI
    matrix = fitz.Matrix(zoom, zoom)

    for page_num, page in enumerate(doc):
        # Run sync render in threadpool to avoid blocking event loop
        png_bytes = await asyncio.to_thread(
            lambda p=page: p.get_pixmap(matrix=matrix).tobytes("png")
        )
        baseline = baselines[page_num]
        await deque.push(PageImage(
            page_num=page_num,
            png_bytes=png_bytes,
            width=int(page.rect.width * zoom),
            height=int(page.rect.height * zoom),
            dpi=dpi,
            is_scanned=baseline.is_scanned,
            baseline_text=baseline.text,
        ))

    doc.close()
```

**Key points**:
- The rasterizer is the **only producer**.
- It pushes pages one at a time, in order, but does NOT block on consumers.
- DPI is configurable (default 200); 200 DPI balances quality vs payload size for inline image sending. Bump to 300 for documents with very small print.
- No batching — each `PageImage` is one unit of work for one worker.

---

### Phase 2: Per-Page Extraction Workers (LangGraph)

**File**: `app/agents/graph.py`

This is the heart of the pipeline. Each page flows through a tiny state machine: extractor → validator → (pass | retry | escalate).

#### LangGraph State

```python
class PageState(TypedDict, total=False):
    # Identity
    page_num: int
    png_bytes: bytes              # the rendered page image
    is_scanned: bool

    # Reference for validator
    baseline_text: str            # from Phase 0

    # Extractor output (replaced each iteration)
    extracted: dict               # {markdown, tables, images, cross_page_notes}

    # Validator output
    validation: dict              # {passed, confidence, gaps[]}

    # Control
    iteration: int                # 1, 2, or 3
    final: dict                   # locked in when passed or after iter3

    # Observability
    trace: list[dict]
```

#### Graph Flow

```
START → extractor (Flash) → validator (Flash) → CONDITIONAL
                                                    │
                              ┌─────────────────────┼─────────────────────┐
                              │                     │                     │
                          passed             iter < 2 & failed     iter == 2 & failed
                              │                     │                     │
                              ▼                     ▼                     ▼
                             END               extractor          escalator (Pro)
                                                                          │
                                                                          ▼
                                                                         END
```

**Iteration cap = 3 total extractor calls per page**:
- iter 1: Flash 2.5 — extract from scratch
- iter 2: Flash 2.5 — re-extract with validator gap feedback (if iter 1 failed)
- iter 3: Pro 2.5 — final escalation (if iter 2 still failed). No further validation. Output is locked.

#### Agent Details

**Extractor** (`extractor.py`):
- Input: PNG bytes (inline, base64) + iteration number + previous gaps (if retry)
- Model: `gemini-2.5-flash` for iter 1 & 2, `gemini-2.5-pro` for iter 3
- Output (strict JSON):
  ```json
  {
    "page_num": 15,
    "markdown": "## Section Heading\n\n...",
    "tables": [
      {"id": "t1", "markdown": "|...|", "json_data": {...},
       "continues_on_next_page": false}
    ],
    "images": [
      {"id": "img1", "description": "Well log showing gamma ray track...",
       "location": "center"}
    ],
    "cross_page_notes": "Table T1 appears to continue from previous page"
  }
  ```
- Prompt emphasizes: extract ALL text, preserve numbers/depths/coordinates exactly, describe every image, never invent content.

**Validator** (`validator.py`):
- Input: PNG bytes + extracted JSON + baseline_text
- Model: `gemini-2.5-flash` (always — Pro is only used for escalated extraction, not validation)
- Three sources cross-referenced:
  1. The PNG image (ground truth via vision)
  2. The PyMuPDF baseline_text (independent text extraction)
  3. The extractor's output
- Output:
  ```json
  {
    "passed": false,
    "confidence": 0.62,
    "gaps": [
      "Table rows 5-8 missing from drilling parameters table",
      "Depth value '3,450ft' in baseline not found in extraction",
      "Image at top of page not described"
    ]
  }
  ```
- `passed=true` requires: no critical gaps AND confidence ≥ 0.85

**Escalator** (`escalation.py`):
- Same prompt structure as extractor but uses Pro 2.5 and receives ALL prior gaps from iter 1 & 2 validations
- Output is FINAL — no iter 4

#### Concurrency Control

```python
# In GeminiClient (singleton, shared across all workers)
self._semaphore = asyncio.Semaphore(settings.max_concurrent_pages)  # default 100

# In orchestrator
async def worker(deque: PageDeque, graph) -> None:
    while True:
        page = await deque.pop()
        if page is None:  # sentinel
            return
        result = await graph.ainvoke(PageState(
            page_num=page.page_num,
            png_bytes=page.png_bytes,
            is_scanned=page.is_scanned,
            baseline_text=page.baseline_text,
            iteration=0,
        ))
        results[page.page_num] = result

# Spawn N workers (where N == max_concurrent_pages)
workers = [asyncio.create_task(worker(deque, graph)) for _ in range(N)]
rasterizer = asyncio.create_task(rasterize_pages(...))
await rasterizer
deque.close(num_consumers=N)
await asyncio.gather(*workers)
```

**Performance budget on Tier 3**:
- 100 concurrent workers, ~2-3s per Flash call → 100 pages * 2 calls (extractor+validator) ≈ 200 calls
- Tier 3 Flash 2.5: 4000 RPM = 66 RPS → 200 calls / 66 RPS ≈ 3 seconds at full saturation
- Realistic: ~10-15s for 100 pages in iter 1, +10-15s if many pages need iter 2
- **Target: 100 pages in <30s** even with iter 2 retries on a portion of pages

**Why 100 concurrent and not 200?**
- Tier 3 RPM is 4000 for Flash; we use ~120-200 RPM in practice → massive headroom for headers/network
- 100 in-flight Gemini calls is the sweet spot before connection-pool/socket overhead matters
- Configurable via `Settings.max_concurrent_pages`

---

### Phase 3: Post-Processing

**File**: `app/pipeline/postprocessor.py`

After all 100 workers finish, the orchestrator has a dict `{page_num: PageState}` with each page's final extraction (either Flash-validated or Pro-escalated). The post-processor turns this into the final document artifacts.

1. **Per-page markdown**: Write `output/{doc_name}/pages/page_{N}.md` per page (already produced by extractor; just persisted here).

2. **Cross-page table merging**: Walk pages in order. When page N's last table has `continues_on_next_page=true` AND page N+1's first table has matching column shape, concatenate their rows into a single table and emit `output/{doc_name}/tables/merged_table_{ID}.json`.

3. **Header/footer deduplication**: Detect repeated lines that appear in identical positions across many pages (running headers, page numbers, footers) and strip them from the combined markdown.

4. **Combined document**: Merge all pages into `output/{doc_name}/full_document.md` with:
   - Document title (taken from the first heading in page 1's markdown)
   - Per-page section markers with page numbers
   - Image descriptions rendered as 🖼 blockquotes inline

5. **Table JSON**: Write `output/{doc_name}/tables/page_{N}_table_{M}.json` for every table extracted.

---

### Phase 4: Heading-Aware Chunking

**File**: `app/vectorstore/chunker.py`

- Use `MarkdownHeaderTextSplitter` + `RecursiveCharacterTextSplitter` from langchain
- **Heading-aware**: split primarily on H1/H2/H3 boundaries derived from the markdown itself (no separate structure scan needed — headings are present in the extracted markdown)
- Chunk size: ~1000 tokens, overlap: ~200 tokens
- For chunks containing tables: attach the corresponding table JSON as metadata
- For chunks near images: attach the image description context
- Every chunk carries the nearest heading path as `section_path` for filtered retrieval

---

### Phase 5: Qdrant Ingestion

**File**: `app/vectorstore/qdrant_client.py`

#### Collection Schema

```
Collection: "wellsynthai_documents"

Vector: gemini-embedding-2-preview (768 dims or model-specific)

Payload:
{
  "document_id": str,          # UUID
  "document_name": str,        # Original filename
  "page_number": int,
  "chunk_index": int,
  "content_type": str,         # "text" | "table" | "mixed"
  "section_path": list[str],   # e.g. ["Formation Evaluation", "Gamma Ray Log"] (from MD headings)
  "text_content": str,         # The chunk text
  "table_json": dict | null,   # Structured table data if present
  "image_description": str,    # Image description if present
  "has_table": bool,
  "has_image": bool,
  "total_pages": int,
  "extraction_method": str,    # "flash" | "pro_escalated"
  "iteration_count": int,      # 1, 2, or 3
  "confidence_score": float,
  "created_at": str            # ISO timestamp
}
```

#### Multimodal Storage

- For pages with diagrams/plots: store a rasterized image of that region
- Use Gemini multimodal embeddings for chunks that reference images
- Enables multimodal RAG: text queries can retrieve image-associated chunks

#### Filterable Indexes

Create Qdrant payload indexes on: `document_id`, `section_path`, `content_type`, `has_table`, `extraction_method`. This enables filtered retrieval (e.g., "find all tables extracted by Pro escalation" or "find all chunks under Formation Evaluation").

---

### Logging & Tracing

**File**: `app/utils/logging.py`

Each page gets a trace context with:

```json
{
  "job_id": "uuid",
  "document": "well_report_A.pdf",
  "page_num": 13,
  "is_scanned": false,
  "trace": [
    {"phase": "preprocessing", "method": "pymupdf4llm", "chars": 1420, "time_ms": 12},
    {"phase": "rasterize", "dpi": 200, "png_kb": 184, "time_ms": 38},
    {"phase": "extractor", "model": "flash-2.5", "iteration": 1, "output_chars": 2350, "time_ms": 2100},
    {"phase": "validator", "model": "flash-2.5", "iteration": 1, "passed": false, "confidence": 0.62, "gaps": ["table rows 5-8 missing"], "time_ms": 1800},
    {"phase": "extractor", "model": "flash-2.5", "iteration": 2, "output_chars": 2620, "time_ms": 2200},
    {"phase": "validator", "model": "flash-2.5", "iteration": 2, "passed": true, "confidence": 0.92, "time_ms": 850}
  ],
  "final_iteration": 2,
  "escalated": false,
  "total_time_ms": 7000,
  "total_api_calls": 4,
  "cached_calls": 4,
  "escalated": false,
  "final_confidence": 0.92,
  "token_usage": {
    "input_tokens": 12400,
    "cached_input_tokens": 9800,
    "output_tokens": 3200
  }
}
```

---

### FastAPI Endpoints

**File**: `app/main.py`

| Method | Endpoint               | Description                          |
|--------|------------------------|--------------------------------------|
| POST   | `/ingest`              | Upload PDF, start ingestion job      |
| GET    | `/ingest/{job_id}`     | Get job status + progress            |
| GET    | `/ingest/{job_id}/pages/{n}` | Get extracted markdown for page |
| GET    | `/health`              | Health check (Qdrant, Gemini)        |

---

## Implementation Order

| Step | Task                                                       | Status      |
|------|-----------------------------------------------------------|-------------|
| 1    | Project scaffolding + config + dependencies               | Done (kept) |
| 2    | Singleton GeminiClient with Semaphore(100), no Files API  | Refactor    |
| 3    | Phase 0: PyMuPDF baseline text (drop PaddleOCR)           | Done (kept) |
| 4    | Phase 1: Streaming PageDeque + rasterizer (NEW)           | New         |
| 5    | Phase 2: PageState LangGraph (per-page, 3-iter cap)       | Rewrite     |
| 6    | Extractor / Validator / Escalator nodes (inline PNG)      | Rewrite     |
| 7    | Worker pool (100 consumers + 1 producer)                  | New         |
| 8    | Phase 3: Post-processor (cross-page merge, table JSON)    | Done (kept) |
| 9    | Phase 4: Section-aware MD chunking                        | TODO        |
| 10   | Phase 5: Qdrant client + collection + indexes             | TODO        |
| 11   | Gemini embeddings                                         | TODO        |
| 12   | Pipeline orchestrator end-to-end wiring                   | Refactor    |
| 13   | FastAPI endpoints                                         | Done (kept) |
| 14   | Docker Compose (app + Qdrant)                             | TODO        |
| 15   | Tests (per module)                                        | Update      |

---

## Key Design Decisions

1. **Per-page rasterization, no Files API**: Each page is rendered with PyMuPDF to PNG bytes and sent inline as image data. No upload step, no file lifecycle to manage, no 1000-page hard limit. The rendered PNG IS what Gemini sees — same fidelity as native PDF rendering, works identically for born-digital and scanned pages.

2. **Streaming, not staged**: The rasterizer pushes a `PageImage` to the deque the moment it finishes. 100 worker tasks pop and process concurrently. We never wait for all pages to render before extraction starts — the first worker fires after the first page is ready (typically within ~100ms).

3. **No batching, ever**: One page = one extraction request. Batching multiple pages into one Gemini call dilutes attention and risks losing detail on dense well-report pages (drilling logs, mud tables, coring summaries). Per-page extraction also makes the iteration loop trivial: a single page either passes or retries, with no "3 of 5 pages passed" ambiguity.

4. **3-iteration hard cap**: iter1 Flash, iter2 Flash (with gap feedback), iter3 Pro. No iter 4 — escalator output is locked. This bounds latency and cost: worst case for 100 pages = 200 Flash extracts + 200 Flash validates + up to 100 Pro escalates, well within Tier 3 limits.

5. **Pro is only used for escalation extraction, not validation**: Validator stays on Flash even at iter3. Pro is used only when Flash demonstrably failed twice — this is when its stronger vision and reasoning earns its cost.

6. **Singleton GeminiClient with one Semaphore(100)**: All workers share the same client instance. Concurrency is enforced inside `gemini_client.generate()` via a class-level semaphore so no caller can accidentally exceed Tier 3 limits. The `genai.Client` is thread-safe and connection-pooled.

7. **PyMuPDF baseline as validator's second opinion**: Phase 0 extracts text with `pymupdf4llm.to_markdown(page_chunks=True)` purely so the validator has a non-Gemini reference. The validator can spot Gemini hallucinations (text in extraction not present in baseline AND not visible in image) or omissions (text present in baseline but missing from extraction).

8. **No structure scan, no document-level metadata at extraction time**: v2 used a Phase 1 Gemini call to derive section names and pass them as context to extraction. v3 drops this — Gemini can read section headings directly from the page image, and the post-processor (Phase 3) extracts document structure from the assembled markdown headings instead. Saves a serial round-trip and removes a fragile dependency.

9. **Cross-page table stitching happens in post-processing**: Each extractor sets `continues_on_next_page` and `cross_page_notes`; the post-processor walks consecutive pages and merges tables when adjacent pages flag continuation.

10. **DPI is configurable**: Default 200 DPI. For dense small-print well reports, bump to 300 via `Settings.render_dpi`. PNG bytes scale ~quadratically with DPI — 200 DPI averages ~150-300 KB per A4 page, 300 DPI averages ~400-600 KB. Both fit comfortably in inline image limits.

11. **Tables in dual format**: Markdown for human display, JSON for Qdrant payload. The validator checks both — markdown for shape, JSON for cell values vs baseline numbers.

12. **No PDF splitting**: Files API limits no longer apply. PyMuPDF can render any size PDF page-by-page. Memory stays flat because pages are rendered, pushed, and immediately consumed — they don't accumulate.

---

## What Changed from v2

| Aspect | v2 Plan | v3 Plan | Why |
|--------|---------|---------|-----|
| PDF input method | Native PDF via Files API + explicit cache | Per-page PNG rasterization, sent inline | Files API adds upload latency, cache lifecycle, and a 1000-page limit. Inline images skip all of that and stream to workers immediately. |
| Phase 1 | Doc structure scan via Gemini | Streaming page rasterizer (no LLM) | The structure scan was a serial blocker; in v3 extraction starts the moment the first page is rendered. Section names come from the page itself. |
| Phase 2 | Batch queue (sections of ~5 pages) | PageDeque (one page per item) | Batching dilutes attention. Per-page is simpler, more accurate, and the iteration loop becomes trivial. |
| Phase 3 | LangGraph on PageBatch | LangGraph on PageState (per page) | Each page is its own state machine; 100 instances run concurrently. |
| Iteration cap | 2 Flash retries + 1 Pro escalation = 3 | Same cap of 3 | iter1 Flash, iter2 Flash with gaps, iter3 Pro. iter3 is FINAL. |
| Validator on iter3 | Validates Pro output too | Pro escalation is FINAL, no further validation | Avoids cost spiral; Pro is the most reliable model already. |
| Concurrency | 25 batches × ~5 pages = 125 in flight | 100 workers × 1 page each | Same in-flight count, simpler accounting, predictable Tier 3 utilization. |
| Context caching | Explicit cache of full PDF (~4x cost reduction) | None | No Files API → no cache. With Tier 3 we have RPM headroom and Flash is cheap; the per-call cost is small enough that the cache complexity isn't worth it. |
| Media resolution | Adaptive HIGH/MEDIUM | Fixed via DPI choice (200 default, 300 for small print) | One knob. Setting per-page resolution would require pre-classification we don't have anymore. |
| OCR fallback | PaddleOCR for scanned pages | None — Gemini vision handles scanned pages | The PNG IS the input; Gemini's vision model OCRs scanned content directly. PaddleOCR was a redundant 3rd party. |
| PDF splitting | TOC-aware split at 800 pages | Not needed | No Files API limit. PyMuPDF reads page-by-page from any size PDF. |
| Cross-page tables | Detected by structure scan, used as batch boundary | Detected by extractor (`continues_on_next_page`), merged in post-processor | Removes the structure-scan dependency and works equally well. |
| Target latency (100 pages) | Not specified | < 30 seconds end-to-end | Streaming + 100 concurrent + no upload overhead = budget for ~2 Flash iterations on every page. |
