# Wellsynthai Ingestion Pipeline — Full Implementation Plan (v2)

> **v2 changes**: Major architectural overhaul based on research into Gemini's native PDF processing,
> context caching, long-context capabilities, and multimodal RAG best practices. See "What Changed
> from v1" section at the bottom.

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
│  Pre-Phase: PDF Splitting            │
│  If >1000 pages or >50MB:            │
│  Split into ~800-page chunks         │
│  aligned to TOC section boundaries   │
│  Each chunk processed independently  │
└──────────┬───────────────────────────┘
           │
           ▼  (per chunk)
┌──────────────────────────────────────┐
│  Phase 0: Pre-Processing             │
│  PyMuPDF text extraction per page    │
│  PaddleOCR fallback (scanned pages)  │
│  → baseline_text[] for validation    │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  Phase 1: Document Structure Scan    │
│  Upload full PDF to Gemini Files API │
│  Flash 2.5 → extract TOC, sections, │
│  page ranges, document type          │
│  → DocStructure (cached)             │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  Phase 2: Batch Deque                │
│  Group pages into batches of ~5      │
│  aligned to section boundaries       │
│  asyncio.deque[PageBatch]            │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────┐
│  Phase 3: LangGraph Agent Pipeline (25 concurrent batches)   │
│  Native PDF pages sent directly (no rasterization)           │
│  MEDIA_RESOLUTION_HIGH for complex pages                     │
│  Context caching per batch for extractor+validator reuse     │
│                                                              │
│  ┌─────────────┐    ┌──────────────┐                        │
│  │ Extractor    │───▶│ Validator    │                        │
│  │ Agent        │    │ Agent        │                        │
│  │ (Flash 2.5)  │    │ (Flash 2.5)  │                        │
│  └──────▲──────┘    └──────┬───────┘                        │
│         │                   │                                │
│         │  retry (max 2x)   │                                │
│         └───────────────────┘                                │
│                             │                                │
│                  ┌──────────▼───────┐                        │
│                  │ Escalation        │                        │
│                  │ (Pro 2.5)         │                        │
│                  └──────────────────┘                        │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────┐
│  Phase 4: Post-Processing            │
│  Markdown file output per page       │
│  Cross-page table/section merging    │
│  Recursive MD chunking               │
│  Table JSON attachment               │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  Phase 5: Qdrant Ingestion           │
│  gemini-embedding-2-preview          │
│  Multimodal (text + images)          │
│  Self-hosted Docker                  │
└──────────────────────────────────────┘
```

---

## Tech Stack

| Component            | Technology                                              |
|----------------------|---------------------------------------------------------|
| Language             | Python 3.11+                                            |
| Orchestration        | LangGraph                                               |
| LLM (Agents)        | Gemini 2.5 Flash                                        |
| LLM (Escalation)    | Gemini 2.5 Pro                                          |
| Embeddings           | gemini-embedding-2-preview                              |
| PDF Parsing          | PyMuPDF (fitz) — baseline text only                     |
| OCR Fallback         | PaddleOCR — scanned/image-only pages                    |
| PDF → Gemini         | Gemini Files API (native PDF, no rasterization needed)  |
| Context Caching      | Gemini explicit cache (per-batch, ~4x cost reduction)   |
| Vector DB            | Qdrant (self-hosted Docker)                             |
| API Framework        | FastAPI + Uvicorn                                       |
| Async Runtime        | asyncio + semaphore (25 concurrent)                     |
| Logging/Tracing      | structlog + per-page trace context                      |
| Chunking             | langchain MarkdownTextSplitter                          |
| PDF Splitting        | PyMuPDF (fitz) — TOC-aware splitting for >1000pg PDFs    |

---

## Project Structure

```
wellsynthai/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI app entry point
│   ├── config.py                  # Settings (Gemini keys, Qdrant URL, etc.)
│   ├── models/
│   │   ├── __init__.py
│   │   ├── schemas.py             # Pydantic models (requests, responses, page context)
│   │   └── state.py               # LangGraph state definitions
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── orchestrator.py        # Main pipeline orchestrator (job management)
│   │   ├── preprocessing.py       # Phase 0: PyMuPDF + PaddleOCR baseline extraction
│   │   ├── structure_scan.py      # Phase 1: Doc structure / TOC extraction via Gemini
│   │   ├── batch_queue.py         # Phase 2: Async deque of page batches
│   │   └── postprocessor.py       # Phase 4: Markdown output, cross-page merge, chunking
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── graph.py               # LangGraph workflow definition
│   │   ├── extractor.py           # Extractor agent node
│   │   ├── validator.py           # Validator agent node
│   │   ├── escalation.py          # Pro model escalation node
│   │   └── prompts.py             # All agent system prompts
│   ├── vectorstore/
│   │   ├── __init__.py
│   │   ├── embeddings.py          # Gemini embedding wrapper
│   │   ├── chunker.py             # Recursive MD chunking + table JSON attachment
│   │   └── qdrant_client.py       # Qdrant operations (upsert, collection management)
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── logging.py             # structlog setup with per-page trace
│   │   ├── gemini_client.py       # Shared Gemini API client with rate limiting
│   │   └── pdf_uploader.py        # Gemini Files API upload + caching helpers
│   └── storage/
│       ├── __init__.py
│       └── file_storage.py        # Markdown + image files to disk
├── tests/
│   ├── __init__.py
│   ├── test_preprocessing.py
│   ├── test_agents.py
│   ├── test_chunking.py
│   └── test_pipeline_e2e.py
├── docker-compose.yml             # Qdrant + app services
├── Dockerfile
├── pyproject.toml
├── .env.example
└── README.md
```

---

## Detailed Phase Breakdown

### Phase 0: Baseline Text Extraction (Local, No LLM)

**File**: `app/pipeline/preprocessing.py`

**Purpose**: Produce a cheap, fast baseline text per page that the Validator can use to cross-check Gemini's extraction. This is NOT the primary extraction — Gemini is. This is the **validation reference**.

1. Open PDF with PyMuPDF (`fitz`)
2. For each page:
   - Extract text blocks with `page.get_text("blocks")` — preserves layout coordinates
   - Detect if page is scanned/image-only: if `len(text.strip()) < 50` chars
   - If scanned → run PaddleOCR on `page.get_pixmap(dpi=300)` PNG
3. Store per-page `baseline_text[]` — used later by the Validator as ground truth reference
4. Also extract basic page metadata: page dimensions, rotation, image count

**Key difference from v1**: No LLM-as-Judge here. PyMuPDF/PaddleOCR runs purely locally. The LLM comparison happens inside the Validator agent in Phase 3, which already has the Gemini extraction to compare against. This removes one LLM call per page.

---

### Phase 1: Document Structure Scan (NEW)

**File**: `app/pipeline/structure_scan.py`

**Why this matters**: Well reports have hierarchical structure — title page, TOC, well summary, formation logs, test results, appendices. Page-by-page extraction without understanding this structure loses critical context (e.g., "Table continued from previous page", section headers that apply to multiple pages, cross-references).

1. Upload the full PDF to **Gemini Files API** (supports up to 50MB / 1000 pages)
2. Single Flash 2.5 call with `MEDIA_RESOLUTION_LOW` (cheap, 280 tokens/page):

```
Analyze this document's structure. Return:
{
  "document_type": str,           // e.g. "well_completion_report", "drilling_log"
  "title": str,
  "sections": [
    {
      "name": str,                // e.g. "Well Summary", "Formation Evaluation"
      "page_start": int,
      "page_end": int,
      "content_types": ["text", "table", "diagram", "log_chart"],
      "complexity": "low" | "medium" | "high"
    }
  ],
  "cross_page_tables": [          // Tables that span multiple pages
    {"page_start": int, "page_end": int, "description": str}
  ],
  "key_entities": {               // Document-level metadata
    "well_name": str,
    "operator": str,
    "field": str,
    "dates": [str]
  }
}
```

3. Create an **explicit Gemini cache** of the uploaded PDF file — this cache will be reused by all subsequent extractor/validator calls, giving ~4x input cost reduction
4. The `DocStructure` output drives:
   - **Batch boundaries** in Phase 2 (don't split across table continuations)
   - **Extraction prompts** in Phase 3 (extractor knows what section it's in)
   - **Media resolution** in Phase 3 (HIGH for complex pages, MEDIUM for text-only)
   - **Document-level metadata** for Qdrant payloads in Phase 5

---

### Phase 2: Batch Deque

**File**: `app/pipeline/batch_queue.py`

**Why batches instead of single pages**: 
- Cross-page context: tables span pages, sections span pages, context is lost page-by-page
- Token efficiency: Gemini charges per-request overhead; batching amortizes it
- Cache hits: same PDF file is cached; batching reduces total API calls

```python
from collections import deque
import asyncio

class BatchQueue:
    def __init__(self):
        self._deque: deque[PageBatch] = deque()
        self._event = asyncio.Event()
        self._done = False

    async def push(self, batch: PageBatch): ...
    async def pop(self) -> PageBatch | None: ...
    def mark_done(self): ...

@dataclass
class PageBatch:
    batch_id: int
    page_range: tuple[int, int]     # e.g. (5, 9) = pages 5-9
    section_context: str            # From DocStructure: "Formation Evaluation"
    complexity: str                 # "low" | "medium" | "high"
    baseline_texts: list[str]       # From Phase 0
    media_resolution: str           # HIGH for complex, MEDIUM for text-only
```

**Batch construction algorithm**:
1. Walk `DocStructure.sections` 
2. For each section, create batches of ~5 pages
3. Never split a `cross_page_table` across batches
4. Tag complexity from DocStructure (drives media_resolution)

---

### Phase 3: LangGraph Agent Pipeline

**File**: `app/agents/graph.py`

#### Critical change: Native PDF, no rasterization

**Research finding**: Gemini 2.5+ processes PDFs natively — it extracts embedded text AND uses vision on each page simultaneously. Rasterizing to images and sending PNG bytes:
- Loses native text layer (forces Gemini to OCR text that's already machine-readable)
- Costs more tokens (images are 256+ tokens/page; native PDF text is free on Gemini 3)
- Degrades quality for text-heavy pages

**New approach**: Upload PDF once via Files API → cache it → reference cached PDF + page ranges in each agent call. Gemini sees the native PDF pages directly.

#### Context Caching Strategy

```python
# Upload PDF once (done in Phase 1)
pdf_file = client.files.upload(file=pdf_path, config={"mime_type": "application/pdf"})

# Create explicit cache (done in Phase 1, reused by all Phase 3 calls)
cache = client.caches.create(
    model="models/gemini-2.5-flash",
    config=types.CreateCachedContentConfig(
        display_name=f"wellsynthai_{doc_id}",
        system_instruction=EXTRACTION_SYSTEM_PROMPT,
        contents=[pdf_file],
        ttl="3600s",  # 1 hour, enough for full pipeline
    )
)
# All subsequent calls reference this cache → ~4x cheaper input tokens
```

#### LangGraph State

```python
class BatchState(TypedDict):
    batch_id: int
    page_range: tuple[int, int]
    section_context: str           # Section name from DocStructure
    doc_structure: dict            # Full DocStructure for cross-reference
    baseline_texts: list[str]      # PyMuPDF/PaddleOCR per page (for validation)
    media_resolution: str          # HIGH or MEDIUM
    extracted_pages: list[dict]    # Per-page extraction results
    validation_result: dict        # From validator
    iteration_count: int           # Retry count (max 2)
    escalated: bool
    final_pages: list[dict]        # Final output per page
    trace_log: list[dict]
```

#### Graph Flow

```
START → extractor → validator → CONDITIONAL
                                    │
                       ┌────────────┼────────────┐
                       │            │            │
                    PASS        RETRY         ESCALATE
                     │         (≤2x)         (Pro 2.5)
                     │            │            │
                     ▼            ▼            ▼
                    END      extractor        END
```

#### Agent Details

**Extractor Agent** (`extractor.py` — Flash 2.5):
- Input: cached PDF reference + page range + section context + DocStructure + baseline_texts + gap feedback (if retry)
- Uses `MEDIA_RESOLUTION_HIGH` for complex pages (diagrams, tables), `MEDIA_RESOLUTION_MEDIUM` for text-only
- The section context tells the extractor: "You are extracting pages 15-19 which belong to the 'Formation Evaluation' section"
- Output per page in batch:
  ```json
  {
    "page_num": 15,
    "markdown": "## Formation Evaluation\n\n...",
    "tables": [{"id": "t1", "markdown": "|...|", "json": {...}, "continues_on_next_page": false}],
    "images": [{"id": "img1", "description": "Well log showing gamma ray...", "location": "center"}],
    "cross_page_notes": "Table T1 continues from page 14"
  }
  ```
- On retry: receives validator's gap list per page to focus extraction

**Validator Agent** (`validator.py` — Flash 2.5, escalates to Pro 2.5):
- Input: cached PDF reference + page range + extracted_pages + baseline_texts
- For each page in batch, cross-references THREE sources:
  1. **PDF page visual** (ground truth — Gemini sees the actual page)
  2. **baseline_text** (PyMuPDF/PaddleOCR — independent extraction)
  3. **extracted_pages** (what the extractor produced)
- Evaluation criteria:
  - **Text completeness**: Does extraction contain all text visible on the page? Cross-check against baseline_text
  - **Table integrity**: Are all rows/columns present? Do numbers match?
  - **Image/diagram coverage**: Are all visual elements described?
  - **Cross-page coherence**: Do continued tables/sections connect properly?
  - **Factual accuracy**: Do extracted numbers, names, depths match what's in the PDF?
- Output:
  ```json
  {
    "pass": false,
    "overall_confidence": 0.85,
    "page_results": [
      {
        "page_num": 15,
        "pass": true,
        "confidence": 0.95,
        "gaps": []
      },
      {
        "page_num": 16,
        "pass": false,
        "confidence": 0.6,
        "gaps": ["Table rows 5-8 missing from drilling parameters table",
                 "Depth value '3,450ft' in baseline not found in extraction"]
      }
    ]
  }
  ```
- If any page fails: `pass=False`, route back to extractor with gap details
- If `iteration_count >= 2` → escalate to Pro 2.5

**Escalation** (`escalation.py` — Pro 2.5):
- Gets full context: cached PDF + page range + all previous attempts + all gap feedback
- Pro 2.5 has stronger vision and reasoning — handles edge cases like:
  - Handwritten annotations (Gemini docs note: "models might hallucinate on handwritten text")
  - Extremely dense tables
  - Overlapping text/image layouts
- Output becomes final (no further validation to avoid cost spiral)

#### Concurrency Control

```python
semaphore = asyncio.Semaphore(25)

async def process_batch(batch: PageBatch, cache: CachedContent):
    async with semaphore:
        result = await graph.ainvoke(BatchState(...))
        return result
```

25 concurrent batches × ~5 pages each = ~125 pages in flight.
Each batch makes ~2 cached API calls (extractor + validator) = 50 RPM in happy path.
Flash 2.5 Tier 3: 4000 RPM → massive headroom.
Cached calls are ~4x cheaper on input tokens.

---

### Phase 4: Post-Processing

**File**: `app/pipeline/postprocessor.py`

1. **Cross-page merging**: 
   - Stitch continued tables across batch/page boundaries using `cross_page_notes`
   - Merge section headers that span pages
   - Deduplicate headers/footers

2. **Markdown files**: Write `output/{doc_name}/page_{N}.md` per page

3. **Combined document**: Merge all pages into `output/{doc_name}/full_document.md` with:
   - Document title and metadata from DocStructure
   - Section headings from DocStructure
   - Page separators with page numbers

4. **Table JSON**: Write `output/{doc_name}/tables/page_{N}_table_{M}.json`

**File**: `app/vectorstore/chunker.py`

- Use `MarkdownTextSplitter` (recursive) from langchain
- **Section-aware chunking**: Use DocStructure section boundaries as primary split points
- Chunk size: ~1000 tokens, overlap: ~200 tokens
- For chunks containing tables: attach the corresponding table JSON as metadata
- For chunks near images: attach the image description context
- Every chunk gets `section_name` from DocStructure for better filtered retrieval

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
  "document_type": str,        # From DocStructure: "well_completion_report", etc.
  "page_number": int,
  "chunk_index": int,
  "content_type": str,         # "text" | "table" | "mixed"
  "section_name": str,         # From DocStructure: "Formation Evaluation"
  "section_heading": str,      # Nearest heading in markdown
  "text_content": str,         # The chunk text
  "table_json": dict | null,   # Structured table data if present
  "image_description": str,    # Image description if present
  "has_table": bool,
  "has_image": bool,
  "total_pages": int,
  "well_name": str,            # From DocStructure key_entities
  "operator": str,             # From DocStructure key_entities
  "field": str,                # From DocStructure key_entities
  "extraction_method": str,    # "flash" | "pro_escalated"
  "confidence_score": float,
  "created_at": str            # ISO timestamp
}
```

#### Multimodal Storage

- For pages with diagrams/plots: store a rasterized image of that region
- Use Gemini multimodal embeddings for chunks that reference images
- Enables multimodal RAG: text queries can retrieve image-associated chunks

#### Filterable Indexes

Create Qdrant payload indexes on: `document_id`, `document_type`, `section_name`, `well_name`, `content_type`, `has_table`. This enables filtered retrieval (e.g., "find all formation evaluation tables for well X").

---

### Logging & Tracing

**File**: `app/utils/logging.py`

Each page gets a trace context with:

```json
{
  "job_id": "uuid",
  "document": "well_report_A.pdf",
  "batch_id": 3,
  "page_range": [11, 15],
  "section": "Formation Evaluation",
  "trace": [
    {"phase": "preprocessing", "method": "pymupdf", "page": 11, "chars": 1420, "time_ms": 12},
    {"phase": "preprocessing", "method": "paddleocr_fallback", "page": 13, "chars": 890, "time_ms": 340},
    {"phase": "structure_scan", "model": "flash-2.5", "resolution": "LOW", "time_ms": 2100, "cached": false},
    {"phase": "extractor", "model": "flash-2.5", "iteration": 1, "pages": 5, "resolution": "HIGH", "cached": true, "output_chars": 8400, "time_ms": 3200},
    {"phase": "validator", "model": "flash-2.5", "iteration": 1, "cached": true, "pass": false, "failed_pages": [13], "gaps": ["table rows 5-8 missing"], "time_ms": 1800},
    {"phase": "extractor", "model": "flash-2.5", "iteration": 2, "pages": 1, "cached": true, "output_chars": 2350, "time_ms": 1100},
    {"phase": "validator", "model": "flash-2.5", "iteration": 2, "cached": true, "pass": true, "confidence": 0.92, "time_ms": 850}
  ],
  "total_time_ms": 9402,
  "total_api_calls": 5,
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

| Step | Task                                            | Estimated Complexity |
|------|-------------------------------------------------|---------------------|
| 1    | Project scaffolding + config + dependencies      | Low                 |
| 2    | Gemini client + Files API upload + rate limiting | Medium              |
| 3    | PyMuPDF baseline extraction + PaddleOCR fallback | Medium              |
| 4    | Document structure scan (Phase 1)                | Medium              |
| 5    | Explicit context caching setup                   | Medium              |
| 6    | Batch queue with section-aware grouping          | Medium              |
| 7    | Extractor agent + prompts (cached PDF)           | High                |
| 8    | Validator agent + retry logic                    | High                |
| 9    | Pro escalation node                              | Medium              |
| 10   | LangGraph wiring (full graph)                    | Medium              |
| 11   | Cross-page merging + markdown output             | High                |
| 12   | Section-aware recursive MD chunking + table JSON | Medium              |
| 13   | Qdrant client + collection + indexes             | Medium              |
| 14   | Gemini embeddings + multimodal ingestion         | Medium              |
| 15   | Pipeline orchestrator (end-to-end)               | High                |
| 16   | FastAPI endpoints                                | Medium              |
| 17   | Docker Compose (app + Qdrant)                    | Low                 |
| 18   | Tests                                            | Medium              |

---

## Key Design Decisions

1. **Native PDF over rasterization**: Gemini 2.5+ processes PDFs natively — extracts embedded text AND applies vision. Rasterizing to PNG loses the native text layer, forces re-OCR of machine-readable text, and costs more tokens. Google's own docs say: "Use PDFs created with text rendered as text instead of scanned images. This provides optimal results for text-heavy documents." We send the PDF directly via Files API.

2. **Document structure scan first**: A single cheap call (MEDIA_RESOLUTION_LOW = 280 tokens/page) gives us TOC, sections, cross-page tables, and document metadata. This context makes every downstream extraction call smarter — the extractor knows it's in "Formation Evaluation", knows the table continues from page 14, knows the document is a "well completion report". Without this, each page is extracted in isolation.

3. **Batch processing over page-by-page**: Batches of ~5 pages aligned to section boundaries solve the cross-page problem (tables spanning pages, sections spanning pages). Also reduces total API calls by ~5x vs page-by-page.

4. **Context caching**: Upload PDF once, cache it, reference cache in all subsequent calls. Gemini 2.5 explicit caching gives ~4x input cost reduction. For a 200-page PDF with ~40 batches × 2-4 calls each, this is massive savings. Implicit caching (auto-enabled on 2.5+) gives additional hits for similar-prefix requests.

5. **PyMuPDF/PaddleOCR as validation reference, not primary extraction**: v1 used PyMuPDF as input to the extractor. v2 uses it purely as an independent baseline for the Validator to cross-check against. Gemini sees the native PDF directly — PyMuPDF text is a "second opinion" to catch extraction gaps, not the primary source.

6. **No LLM-as-Judge in Phase 0**: v1 spent an LLM call per page comparing PyMuPDF vs PaddleOCR output. Unnecessary — the Validator in Phase 3 already compares Gemini's extraction against baseline_text. Removing this saves 1 API call per page.

7. **MEDIA_RESOLUTION per complexity**: Google's docs show MEDIUM (560 tokens/page) is optimal for standard documents, but HIGH (1120 tokens/page) is needed for complex visual analysis, chart reading, dense tables. DocStructure tags page complexity → we use HIGH only where needed, saving tokens on simple text pages.

8. **Section-aware chunking**: Standard recursive chunking doesn't know about document structure. Our chunker uses DocStructure section boundaries as primary split points — a chunk never spans two unrelated sections. Each chunk carries `section_name` metadata enabling filtered retrieval in Qdrant.

9. **2 agents, not 3**: The identifier agent is redundant — Gemini sees the native PDF page directly and can identify + extract in one pass. The baseline_text provides additional context about what's on the page.

10. **Tables as dual format**: Markdown for human readability in files; JSON for structured retrieval in Qdrant (attached to chunk metadata). This means a query like "what was the casing depth?" can retrieve the exact table cell.

11. **Escalation is terminal**: Pro 2.5 output is final — no further validation loop to avoid cost explosion on edge cases.

12. **PDF splitting for large documents**: Gemini Files API has a hard limit of 1000 pages / 50 MB per upload. Well reports can reach 6000+ pages. We split at ~800-page boundaries, preferring TOC section breaks. Each chunk is processed independently through the full pipeline, then merged in post-processing. Structure scan only needs the first ~20 pages (title + TOC) regardless of document size.

---

## What Changed from v1

| Aspect | v1 Plan | v2 Plan | Why |
|--------|---------|---------|-----|
| PDF input method | Rasterize to 300 DPI PNG images, send as image bytes | Native PDF via Files API (no rasterization) | Gemini processes PDFs natively — extracts text layer + applies vision. PNG loses native text, costs more tokens, degrades quality on text-heavy pages |
| Phase 0 | PyMuPDF + PaddleOCR + LLM-as-Judge comparison | PyMuPDF + PaddleOCR only (no LLM call) | LLM comparison is redundant — Validator in Phase 3 already does this. Saves 1 API call per page |
| Phase 1 | Rasterization (300 DPI) | Document Structure Scan (TOC, sections, cross-page tables) | Structure awareness is critical for extraction quality. Tables span pages, sections span pages — without this context, extraction is blind |
| Processing unit | Single page | Batch of ~5 pages (section-aligned) | Cross-page context, fewer API calls, better cache utilization |
| Context caching | Not used | Explicit cache of full PDF, reused by all agent calls | ~4x cheaper input tokens. 200-page PDF with 80+ API calls → huge savings |
| Media resolution | Fixed 300 DPI for all pages | Adaptive: HIGH for complex pages, MEDIUM for text-only | Google recommends MEDIUM for standard docs; HIGH only for dense tables/charts. Saves tokens on simple pages |
| PyMuPDF role | Primary input to extractor | Validation reference for cross-checking | Gemini's native PDF vision is superior to PyMuPDF text + image; PyMuPDF serves as independent "second opinion" |
| Chunking | Generic recursive | Section-aware recursive (split on section boundaries) | Chunks don't span unrelated sections; each chunk tagged with section_name for filtered retrieval |
| Qdrant metadata | Basic (page, doc, type) | Rich (section_name, well_name, operator, document_type, filterable indexes) | DocStructure provides document intelligence for free — use it in retrieval |
| Cross-page handling | Not addressed | Explicit: cross_page_tables in DocStructure, cross_page_notes in extraction, merging in post-processing | This was the biggest gap in v1 — well reports frequently have tables spanning 3-5 pages |
| Cost per 200-page PDF | ~400+ uncached API calls | ~1 structure scan + ~80 cached calls (~4x cheaper each) | Roughly 60-70% cost reduction |
