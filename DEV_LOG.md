# Wellsynthai — Development Log

> Single development log tracking all implementation progress, decisions, and issues.

---

## 2026-04-22 — Session 1: Project Scaffolding + Phase 0

### Step 1: Project Scaffolding (PLAN Step 1)

**Files created:**
- `pyproject.toml` — Dependencies: FastAPI, google-genai, LangGraph, PyMuPDF, PaddleOCR, Qdrant, structlog
- `.env.example` — Environment variable template
- `.gitignore` — Python/IDE/data ignores
- `Dockerfile` — Python 3.11-slim with system deps for PyMuPDF/PaddleOCR
- `docker-compose.yml` — Qdrant + app services

**Package structure created:**
```
app/
├── __init__.py
├── main.py              ← FastAPI entry point with /health endpoint
├── config.py            ← pydantic-settings based config (single source of truth)
├── models/
│   ├── schemas.py       ← All Pydantic models (PageBaseline, DocStructure, PageExtraction, etc.)
│   └── state.py         ← LangGraph BatchState TypedDict
├── pipeline/
│   └── preprocessing.py ← Phase 0: PyMuPDF + PaddleOCR baseline extraction
├── agents/              ← (stubs — Phase 3)
├── vectorstore/         ← (stubs — Phase 5)
├── utils/
│   ├── logging.py       ← structlog setup with bound context
│   ├── gemini_client.py ← Gemini SDK wrapper with rate limiting + retry
│   └── pdf_uploader.py  ← Files API upload + cache management
└── storage/
    └── file_storage.py  ← Async file I/O for markdown/JSON/images
tests/
├── conftest.py          ← Shared fixtures (sample_pdf_path, empty_pdf_path)
└── test_preprocessing.py ← Phase 0 tests (11 test cases)
```

**Design decisions:**
- `config.py` uses `pydantic-settings` — env vars are the single source of truth, no hardcoded values
- `gemini_client.py` wraps the Google GenAI SDK — rest of codebase never imports `google.genai` directly, enabling future model swaps
- `schemas.py` defines all data models upfront — every phase produces and consumes typed Pydantic models
- OCR engine is lazy-loaded in `preprocessing.py` — no PaddleOCR import cost when all pages have text

### Step 2: Phase 0 — Baseline Text Extraction (PLAN Steps 2-3)

**Implementation: `app/pipeline/preprocessing.py`**

- `Preprocessor.extract(pdf_path)` → `list[PageBaseline]`
- PyMuPDF extracts text blocks per page
- Pages with < 50 chars flagged as `is_scanned=True`
- PaddleOCR fallback at 300 DPI for scanned pages (lazy-loaded)
- Runs in `asyncio.to_thread` to avoid blocking the event loop
- Page metadata collected: dimensions, image count, extraction method

**Tests: `tests/test_preprocessing.py`**
- 11 test cases covering: text extraction, table-like content, sparse page detection, blank page detection, page dimensions, error handling, OCR disable mode
- Uses in-memory PDFs generated via PyMuPDF fixtures (no external test files)
- **Result: 11/11 passed (0.74s)**

**Notes:**
- PaddleOCR/PaddlePaddle not available for Python 3.14 on Windows — OCR fallback tests use `use_ocr=False`. Will test OCR path when PaddlePaddle wheels are available or on Linux.
- hatchling editable install had a compatibility issue with Python 3.14 — deps installed manually. Not a blocker.

---

### Step 2b: Pre-Phase — PDF Splitting for Large Documents

**Problem identified:** Gemini Files API hard limit is **1000 pages / 50 MB per upload**. User's well reports can reach **6000+ pages**. This was not addressed in the original plan.

**Research confirmed (Google docs, updated 2026-03-25):**
> "Gemini supports PDF files up to 50MB or 1000 pages. This limit applies to both inline data and Files API uploads. Each document page is equivalent to 258 tokens."

**Decision: `requirements.txt` alongside `pyproject.toml`**
- `pyproject.toml` remains the source of truth (PEP 621 standard)
- Added `requirements.txt` for Docker builds — faster, no build backend needed
- Updated `Dockerfile` to `COPY requirements.txt` + `pip install -r`

**Implementation: `app/utils/pdf_splitter.py`**

- `PDFSplitter.split(pdf_path, output_dir)` → `SplitResult`
- If PDF ≤ 1000 pages AND ≤ 50MB → returns original file as single chunk (no copy)
- If exceeds → splits into sub-PDFs of ~800 pages (configurable via `pdf_split_target_pages`)
- **TOC-aware splitting**: Uses PyMuPDF's `get_toc()` to find level-1/2 section boundaries, prefers splitting at section starts
- **Nearest-break search**: Looks for TOC breaks within ±20% of the target split point
- **Min-chunk merging**: Trailing chunks smaller than `min_pages` (default 50) get merged into previous chunk to avoid tiny fragments
- **Hard max enforcement**: No chunk ever exceeds `max_pages` (default 1000)
- Each chunk is a valid PDF file with `chunk.page_count`, `chunk.file_path`, `chunk.toc_sections`

**Config additions (`app/config.py`):**
- `pdf_max_pages: int = 1000`
- `pdf_max_size_mb: int = 50`
- `pdf_split_target_pages: int = 800`
- `pdf_split_min_pages: int = 50`

**Tests: `tests/test_pdf_splitter.py`**
- 15 test cases across 5 test classes:
  - `TestPDFSplitterNoSplitNeeded` (3): small PDF, at-limit, file-not-found
  - `TestPDFSplitterBasicSplitting` (6): chunk count, contiguity, max enforcement, file validity, content match, trailing merge
  - `TestPDFSplitterWithTOC` (2): TOC boundary alignment, section tracking
  - `TestPDFSplitterLargeDocument` (1): **6000-page simulation** — verifies correct chunking
  - `TestComputeBoundaries` (3): algorithm unit tests
- **Result: 15/15 passed (4.18s)**
- **Full suite: 26/26 passed (4.75s)**

**PLAN.md updated:**
- Added "Pre-Phase: PDF Splitting" to architecture diagram
- Added `PDF Splitting` to Tech Stack table
- Added design decision #12 documenting the splitting strategy

---
