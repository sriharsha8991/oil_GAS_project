# Wellsynthai Ingestion Pipeline — On-Premise Plan

> **Goal**: A fully self-hosted, zero-cloud-dependency document ingestion pipeline for well report
> PDFs. No API keys, no external calls, no vendor lock-in. Everything runs on your own hardware.

---

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
│  Phase 0: Primary Extraction         │
│  MinerU 3.1 (PDF → Markdown + JSON)  │
│  Layout detection, table structure,  │
│  formula→LaTeX, OCR, cross-page      │
│  table merging — all built-in        │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  Phase 1: Document Structure Scan    │
│  Qwen2.5-VL-7B via vLLM             │
│  Rasterize page 1 + TOC page(s)     │
│  → DocStructure (sections, metadata) │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  Phase 2: Validation & Repair        │
│  Compare MinerU output vs page image │
│  Qwen2.5-VL-7B validates only       │
│  flagged/complex pages               │
│  → Repair gaps in-place              │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  Phase 3: Post-Processing            │
│  Markdown file output per page       │
│  Cross-page table/section merging    │
│  Section-aware recursive chunking    │
│  Table JSON attachment               │
└──────────┬───────────────────────────┘
           │
           ▼
┌──────────────────────────────────────┐
│  Phase 4: Vector Ingestion           │
│  BGE-M3 embeddings (self-hosted)     │
│  Qdrant (self-hosted Docker)         │
└──────────────────────────────────────┘
```

**Key architectural difference from the Gemini plan**: MinerU 3.1 is the **primary extractor**, not a VLM. It is a purpose-built document parsing engine (86-90+ on OmniDocBench) that handles layout detection, table structure recognition, formula conversion, OCR, and cross-page table merging natively. The VLM (Qwen2.5-VL-7B) is only used for **structure scanning** and **validation of flagged pages** — not for page-by-page extraction. This drastically reduces GPU requirements.

---

## Tech Stack

| Component              | Technology                                                        | License      |
|------------------------|-------------------------------------------------------------------|--------------|
| Language               | Python 3.11+                                                      | —            |
| **Primary Extractor**  | MinerU 3.1 (opendatalab/MinerU)                                   | Apache 2.0*  |
| **VLM (validation)**   | Qwen2.5-VL-7B-Instruct                                           | Qwen License |
| **VLM Serving**        | vLLM (with OpenAI-compatible API)                                 | Apache 2.0   |
| Orchestration          | LangGraph                                                         | MIT          |
| PDF Baseline/Fallback  | PyMuPDF (fitz) — for scanned page detection                       | AGPL/Commercial |
| OCR (MinerU internal)  | PaddleOCR / MinerU VLM backend — 109 languages                    | Apache 2.0   |
| Embeddings             | BGE-M3 (BAAI/bge-m3) — multilingual, 1024-dim                    | MIT          |
| Vector DB              | Qdrant (self-hosted Docker)                                       | Apache 2.0   |
| API Framework          | FastAPI + Uvicorn                                                  | MIT          |
| Async Runtime          | asyncio + semaphore                                                | stdlib       |
| Logging/Tracing        | structlog + per-page trace context                                 | MIT          |
| Chunking               | langchain MarkdownTextSplitter                                     | MIT          |
| Container Runtime      | Docker Compose                                                     | Apache 2.0   |

> \* MinerU uses "MinerU Open Source License" based on Apache 2.0 with additional terms for the VLM model.

---

## Hardware Requirements

### Minimum (CPU-only MinerU + single GPU for VLM)

| Resource | Spec | Purpose |
|----------|------|---------|
| CPU | 16+ cores | MinerU pipeline backend, FastAPI, embedding inference |
| RAM | 64 GB | MinerU processing, model loading |
| GPU | 1× NVIDIA A100 40GB (or RTX 4090 24GB) | Qwen2.5-VL-7B via vLLM + BGE-M3 |
| Storage | 500 GB SSD | PDFs, markdown output, Qdrant data, model weights |

**Throughput estimate (minimum)**: ~5-10 pages/minute with CPU-only MinerU, VLM validation on ~20% of pages.

### Recommended (GPU MinerU + dedicated VLM GPU)

| Resource | Spec | Purpose |
|----------|------|---------|
| CPU | 32+ cores | FastAPI, async orchestration, post-processing |
| RAM | 128 GB | Concurrent pipeline processing |
| GPU 1 | 1× NVIDIA A100 40GB | MinerU VLM backend (MinerU2.5-Pro model, 8GB VRAM min) |
| GPU 2 | 1× NVIDIA A100 40GB | Qwen2.5-VL-7B via vLLM (validation + structure scan) |
| GPU 3 | (optional) 1× NVIDIA T4 16GB | BGE-M3 embedding inference (or share with GPU 2) |
| Storage | 1 TB NVMe SSD | PDFs, output, Qdrant, model weights (~30GB total) |

**Throughput estimate (recommended)**: ~30-50 pages/minute with GPU MinerU, parallel VLM validation.

### Scaling Option: Qwen2.5-VL-72B for maximum quality

If quality needs to match Gemini Pro 2.5 exactly:

| Resource | Spec | Purpose |
|----------|------|---------|
| GPU | 2× A100 80GB (tensor parallel) or 4× A100 40GB | Qwen2.5-VL-72B via vLLM |

Qwen2.5-VL-72B scores **96.4 on DocVQA** (comparable to Gemini). But the 7B model is sufficient for validation-only use since MinerU does the heavy lifting.

---

## Project Structure

```
wellsynthai/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI app entry point
│   ├── config.py                  # Settings (vLLM URL, Qdrant URL, MinerU config, etc.)
│   ├── models/
│   │   ├── __init__.py
│   │   ├── schemas.py             # Pydantic models (requests, responses, page context)
│   │   └── state.py               # LangGraph state definitions
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── orchestrator.py        # Main pipeline orchestrator (job management)
│   │   ├── mineru_extractor.py    # Phase 0: MinerU 3.1 primary extraction
│   │   ├── structure_scan.py      # Phase 1: Doc structure via Qwen2.5-VL-7B
│   │   ├── postprocessor.py       # Phase 3: Markdown output, cross-page merge, chunking
│   │   └── page_classifier.py     # Classify pages for validation routing
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── graph.py               # LangGraph workflow (validation + repair)
│   │   ├── validator.py           # Validator agent node (Qwen2.5-VL-7B)
│   │   ├── repair.py              # Repair agent node (Qwen2.5-VL-7B or 72B)
│   │   └── prompts.py             # All agent system prompts
│   ├── vectorstore/
│   │   ├── __init__.py
│   │   ├── embeddings.py          # BGE-M3 embedding wrapper (local inference)
│   │   ├── chunker.py             # Section-aware MD chunking + table JSON
│   │   └── qdrant_client.py       # Qdrant operations (upsert, collection management)
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── logging.py             # structlog setup with per-page trace
│   │   ├── vllm_client.py         # OpenAI-compatible client for vLLM
│   │   └── rasterizer.py          # PyMuPDF page → PNG for VLM input
│   └── storage/
│       ├── __init__.py
│       └── file_storage.py        # Markdown + image + table files to disk
├── tests/
│   ├── __init__.py
│   ├── test_mineru.py
│   ├── test_agents.py
│   ├── test_chunking.py
│   └── test_pipeline_e2e.py
├── docker-compose.yml             # MinerU + vLLM + Qdrant + app
├── Dockerfile
├── pyproject.toml
├── .env.example
└── README.md
```

---

## Detailed Phase Breakdown

### Phase 0: Primary Extraction via MinerU 3.1

**File**: `app/pipeline/mineru_extractor.py`

**Why MinerU as primary extractor instead of VLM page-by-page**:
- MinerU scores **86-90+ on OmniDocBench** — purpose-built for exactly this task
- **Cross-page table merging is built-in** — the hardest problem in well report extraction, solved natively
- Handles layout detection, table structure recognition, formula→LaTeX, reading order, header/footer removal
- Runs on **CPU** for pipeline backend (no GPU needed for base extraction)
- Outputs Markdown AND JSON simultaneously — we get both formats for free
- 109-language OCR via PaddleOCR (built-in)
- Processing is **deterministic** — same input always gives same output (unlike LLM-based extraction)

**Two MinerU backends available**:

| Backend | Model | VRAM | Accuracy | Speed |
|---------|-------|------|----------|-------|
| Pipeline (CPU/GPU) | Traditional layout + OCR models | 4GB (GPU) or 0 (CPU) | 86+ OmniDocBench | Fast |
| VLM | MinerU2.5-Pro-2604-1.2B | 8GB | 90+ OmniDocBench | Slower |

**Recommended**: Start with VLM backend for higher accuracy if you have a spare GPU. Fall back to pipeline backend for CPU-only deployments.

**Integration approach**:

```python
# Option A: MinerU Python API
from magic_pdf.data.data_reader_writer import FileBasedDataWriter, FileBasedDataReader
from magic_pdf.pipe.UNIPipe import UNIPipe

# Option B: MinerU HTTP API (FastAPI built-in)
import httpx

class MinerUExtractor:
    """Primary document extraction via MinerU 3.1."""

    def __init__(self, mode: str = "vlm", mineru_api_url: str | None = None):
        self.mode = mode                    # "vlm" | "pipeline"
        self.api_url = mineru_api_url       # If using MinerU's built-in FastAPI

    async def extract(self, pdf_path: Path) -> MinerUResult:
        """
        Extract full PDF → structured output.
        Returns markdown per page, tables (JSON + MD), images, formulas.
        Cross-page tables are already merged by MinerU.
        """
        if self.api_url:
            return await self._extract_via_api(pdf_path)
        return await asyncio.to_thread(self._extract_local, pdf_path)

    def _extract_local(self, pdf_path: Path) -> MinerUResult:
        """Run MinerU locally (blocking, wrapped in asyncio.to_thread)."""
        reader = FileBasedDataReader(str(pdf_path.parent))
        pdf_bytes = reader.read(pdf_path.name)

        pipe = UNIPipe(pdf_bytes, model_list=[], image_writer=image_writer)
        pipe.pipe_classify()
        pipe.pipe_analyze()
        pipe.pipe_parse()

        md_content = pipe.pipe_mk_markdown(image_dir, drop_mode="none")
        return self._parse_mineru_output(md_content, pipe)
```

**Output per page**:

```python
@dataclass
class MinerUPageResult:
    page_num: int
    markdown: str                    # Full page markdown
    tables: list[TableResult]        # Each table as MD + JSON
    images: list[ImageResult]        # Extracted images with bounding boxes
    formulas: list[str]              # LaTeX formulas
    reading_order_blocks: list[dict] # Layout blocks in reading order
    is_scanned: bool                 # Whether page was OCR'd
    confidence_signals: dict         # Internal quality signals from MinerU
```

**What MinerU handles natively (no VLM needed)**:
- ✅ Text extraction with reading order
- ✅ Table structure recognition (rows, columns, spanning cells)
- ✅ Cross-page table merging (detects "Table continued" patterns)
- ✅ Formula → LaTeX conversion
- ✅ Image extraction with bounding boxes
- ✅ Header/footer detection and removal
- ✅ Layout analysis (columns, sidebars, captions)
- ✅ Truncated paragraph merging across pages
- ✅ Multi-language OCR (109 languages via PaddleOCR)

---

### Phase 1: Document Structure Scan via VLM

**File**: `app/pipeline/structure_scan.py`

**Why still use a VLM here**: MinerU extracts content perfectly but doesn't produce a semantic TOC or understand document-level structure (document type, well name, operator, field). A single VLM call on the title page + TOC page gives us this cheaply.

1. Rasterize page 1 and TOC page(s) via PyMuPDF → PNG at 300 DPI
2. Send to Qwen2.5-VL-7B via vLLM (OpenAI-compatible API):

```python
async def scan_structure(self, pdf_path: Path) -> DocStructure:
    """Single VLM call to extract document structure from title + TOC pages."""

    # Rasterize title page and detected TOC page(s)
    pages_png = self.rasterizer.rasterize_pages(pdf_path, page_nums=[0] + toc_pages)

    messages = [
        {"role": "system", "content": STRUCTURE_SCAN_PROMPT},
        {"role": "user", "content": [
            *[{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
              for b64 in pages_png],
            {"type": "text", "text": (
                "Analyze this document's structure. The document has "
                f"{total_pages} pages. Return JSON with: document_type, title, "
                "sections (name, page_start, page_end, content_types, complexity), "
                "cross_page_tables, key_entities (well_name, operator, field, dates)."
            )}
        ]}
    ]

    response = await self.vllm_client.chat(messages, response_format={"type": "json_object"})
    return DocStructure.model_validate_json(response.content)
```

3. **Cross-reference with MinerU output**: MinerU's heading detection can validate/refine the VLM's section boundaries. If MinerU detected `## Formation Evaluation` on page 15, and the VLM said that section starts on page 14, trust MinerU's page-level precision.

4. DocStructure output (same schema as Gemini plan):

```json
{
  "document_type": "well_completion_report",
  "title": "ABC-1 Well Completion Report",
  "sections": [
    {
      "name": "Well Summary",
      "page_start": 2,
      "page_end": 5,
      "content_types": ["text", "table"],
      "complexity": "low"
    },
    {
      "name": "Formation Evaluation",
      "page_start": 15,
      "page_end": 32,
      "content_types": ["text", "table", "log_chart", "diagram"],
      "complexity": "high"
    }
  ],
  "cross_page_tables": [
    {"page_start": 20, "page_end": 22, "description": "Drilling parameters table"}
  ],
  "key_entities": {
    "well_name": "ABC-1",
    "operator": "XYZ Energy",
    "field": "North Basin",
    "dates": ["2025-01-15", "2025-06-20"]
  }
}
```

---

### Phase 2: Validation & Repair (VLM — selective, not every page)

**File**: `app/agents/graph.py`, `app/agents/validator.py`, `app/agents/repair.py`

**Key design choice**: Unlike the Gemini plan where every page goes through Extractor→Validator, the on-premise plan only sends **flagged pages** to the VLM. MinerU is accurate enough that most pages don't need VLM validation. This keeps GPU costs low.

#### Page Classification (which pages need VLM validation?)

**File**: `app/pipeline/page_classifier.py`

```python
class PageClassifier:
    """Decide which pages need VLM validation based on MinerU confidence signals."""

    def classify(self, page: MinerUPageResult, structure: DocStructure) -> str:
        """Returns 'skip' | 'validate' | 'validate_high_priority'."""

        # Always validate pages tagged as high complexity by DocStructure
        section = self._find_section(page.page_num, structure)
        if section and section.complexity == "high":
            return "validate"

        # Always validate pages with detected tables (well report tables are critical)
        if page.tables and any(t.row_count > 10 for t in page.tables):
            return "validate"

        # Validate if MinerU flagged low confidence
        if page.confidence_signals.get("layout_confidence", 1.0) < 0.8:
            return "validate_high_priority"

        # Validate if page is scanned/OCR'd (higher error risk)
        if page.is_scanned:
            return "validate"

        # Validate pages with diagrams/charts (MinerU extracts images but can't describe them)
        if section and "diagram" in section.content_types or "log_chart" in section.content_types:
            return "validate"

        return "skip"
```

**Expected validation rate**: ~20-40% of pages for typical well reports (text-heavy pages skip VLM entirely).

#### LangGraph Validation Flow

```
MinerU output + flagged pages → validator → CONDITIONAL
                                               │
                                  ┌────────────┼──────────┐
                                  │            │          │
                               PASS        REPAIR      SKIP
                                │            │          │
                                ▼            ▼          ▼
                               END     repair agent    END
                                            │
                                            ▼
                                           END
```

**Validator Agent** (`validator.py` — Qwen2.5-VL-7B via vLLM):
- Input: rasterized page image + MinerU markdown for that page
- Cross-references the page image against MinerU's extraction
- Evaluation criteria (same as Gemini plan):
  - Text completeness
  - Table integrity (all rows/columns, numbers match)
  - Image/diagram coverage
  - Formula accuracy
- Output:

```json
{
  "page_num": 16,
  "pass": false,
  "confidence": 0.7,
  "gaps": [
    "Table rows 5-8 missing from drilling parameters table",
    "Depth value '3,450ft' visible in image not found in extraction"
  ]
}
```

**Repair Agent** (`repair.py` — Qwen2.5-VL-7B, or 72B for hard cases):
- Input: page image + MinerU markdown + validator gaps
- Produces a corrected markdown for only the failing sections
- The repair is **patched into** the MinerU output, not a full re-extraction
- If 7B fails to repair (validator still unhappy after 1 retry) → escalate to 72B if available, otherwise accept with low-confidence flag

```python
async def repair_page(self, page_image: bytes, mineru_md: str, gaps: list[str]) -> str:
    """Repair specific gaps in MinerU extraction using VLM."""
    messages = [
        {"role": "system", "content": REPAIR_PROMPT},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": (
                f"The following extraction has gaps:\n\n"
                f"```markdown\n{mineru_md}\n```\n\n"
                f"Gaps identified:\n" +
                "\n".join(f"- {g}" for g in gaps) +
                "\n\nProvide the corrected markdown for the missing/incorrect sections only."
            )}
        ]}
    ]
    response = await self.vllm_client.chat(messages)
    return response.content
```

#### Concurrency (lower than Gemini plan — GPU is the bottleneck)

```python
# vLLM handles batching internally, but we control submission rate
validation_semaphore = asyncio.Semaphore(8)  # 8 concurrent validation requests

async def validate_page(page: MinerUPageResult, page_image: bytes):
    async with validation_semaphore:
        result = await validator.validate(page_image, page.markdown)
        if not result.passed:
            repaired = await repair_agent.repair(page_image, page.markdown, result.gaps)
            page.markdown = merge_repair(page.markdown, repaired)
        return page
```

8 concurrent validation requests → vLLM batches them efficiently on the GPU. Higher concurrency doesn't help since vLLM is GPU-bound.

---

### Phase 3: Post-Processing

**File**: `app/pipeline/postprocessor.py`

Same as the Gemini plan, with one advantage: **MinerU already handles cross-page table merging**, so this phase is simpler.

1. **Cross-page merging** (lightweight):
   - MinerU's cross-page tables are already merged → just verify continuity
   - Merge section headers across page boundaries using DocStructure
   - Deduplicate headers/footers (MinerU may not fully remove these)

2. **Markdown files**: Write `output/{doc_name}/page_{N}.md` per page

3. **Combined document**: `output/{doc_name}/full_document.md` with:
   - Document title and metadata from DocStructure
   - Section headings from DocStructure
   - Page separators with page numbers

4. **Table outputs**:
   - `output/{doc_name}/tables/page_{N}_table_{M}.md` — markdown table
   - `output/{doc_name}/tables/page_{N}_table_{M}.json` — structured JSON

5. **Image descriptions**: For pages with diagrams/charts that went through VLM validation, store the VLM's image descriptions for embedding enrichment

#### Section-Aware Chunking

**File**: `app/vectorstore/chunker.py`

```python
from langchain.text_splitter import MarkdownTextSplitter

class SectionAwareChunker:
    """Chunk markdown respecting document structure boundaries."""

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        self.splitter = MarkdownTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    def chunk(self, pages: list[PageResult], structure: DocStructure) -> list[Chunk]:
        chunks = []
        for section in structure.sections:
            section_md = self._merge_pages_for_section(pages, section)
            raw_chunks = self.splitter.split_text(section_md)
            for i, text in enumerate(raw_chunks):
                chunks.append(Chunk(
                    text=text,
                    section_name=section.name,
                    page_range=(section.page_start, section.page_end),
                    chunk_index=i,
                    tables=self._find_tables_in_chunk(text, pages),
                    image_descriptions=self._find_images_in_chunk(text, pages),
                ))
        return chunks
```

---

### Phase 4: Vector Ingestion

**File**: `app/vectorstore/embeddings.py`, `app/vectorstore/qdrant_client.py`

#### BGE-M3 Embeddings (self-hosted)

**Why BGE-M3**:
- 1024-dimensional dense vectors
- Multilingual (100+ languages)
- Strong on retrieval benchmarks (MTEB)
- Small enough to run on CPU or share a GPU
- MIT license, no restrictions
- Supports dense + sparse + ColBERT (multi-vector) retrieval

```python
from sentence_transformers import SentenceTransformer

class BGEEmbedder:
    """Local BGE-M3 embedding inference."""

    def __init__(self, device: str = "cuda:2"):  # Or "cpu"
        self.model = SentenceTransformer("BAAI/bge-m3", device=device)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Runs in thread pool to avoid blocking."""
        return await asyncio.to_thread(
            self.model.encode, texts, normalize_embeddings=True
        )
```

**Alternative embedding models** (swap-in replacements):

| Model | Dims | Size | License | Notes |
|-------|------|------|---------|-------|
| BAAI/bge-m3 | 1024 | 568M | MIT | **Recommended**. Dense + sparse + ColBERT |
| Snowflake/snowflake-arctic-embed-l-v2.0 | 1024 | 568M | Apache 2.0 | Strong on MTEB, English-focused |
| nvidia/NV-Embed-v2 | 4096 | 7.8B | CC-BY-NC-4.0 | #1 on MTEB but non-commercial license |
| nomic-ai/nomic-embed-text-v2-moe | 768 | 475M | Apache 2.0 | Mixture of experts, efficient |

#### Qdrant Collection Schema

```
Collection: "wellsynthai_documents"

Vector: BGE-M3 dense (1024 dims)

Payload:
{
  "document_id": str,
  "document_name": str,
  "document_type": str,              // From DocStructure
  "page_number": int,
  "chunk_index": int,
  "content_type": str,               // "text" | "table" | "mixed"
  "section_name": str,               // From DocStructure
  "section_heading": str,            // Nearest heading in markdown
  "text_content": str,               // The chunk text
  "table_json": dict | null,         // Structured table data if present
  "image_description": str | null,   // VLM-generated description if available
  "has_table": bool,
  "has_image": bool,
  "total_pages": int,
  "well_name": str,                  // From DocStructure
  "operator": str,
  "field": str,
  "extraction_method": str,          // "mineru" | "mineru+vlm_repaired"
  "confidence_score": float,
  "vlm_validated": bool,             // Whether VLM validation was performed
  "created_at": str                  // ISO timestamp
}
```

#### Filterable Indexes

Same as Gemini plan: `document_id`, `document_type`, `section_name`, `well_name`, `content_type`, `has_table`.

#### Hybrid Retrieval (BGE-M3 advantage)

BGE-M3 supports dense + sparse retrieval natively. Qdrant supports both vector types. This enables:

```python
# Dense (semantic similarity) + Sparse (keyword matching) fusion
# Useful for well reports where exact terms matter (well names, depths, formations)
collection_config = {
    "vectors": {
        "dense": {"size": 1024, "distance": "Cosine"},
    },
    "sparse_vectors": {
        "sparse": {}  # BGE-M3 sparse vectors
    }
}
```

---

## Logging & Tracing

**File**: `app/utils/logging.py`

Same structlog approach as Gemini plan. Each page trace:

```json
{
  "job_id": "uuid",
  "document": "well_report_A.pdf",
  "page": 11,
  "section": "Formation Evaluation",
  "trace": [
    {"phase": "mineru_extraction", "backend": "vlm", "time_ms": 850, "tables_found": 2, "images_found": 1},
    {"phase": "page_classification", "result": "validate", "reason": "high_complexity_section"},
    {"phase": "rasterization", "dpi": 300, "time_ms": 45},
    {"phase": "validator", "model": "qwen2.5-vl-7b", "pass": false, "gaps": ["table rows 5-8 missing"], "time_ms": 1200},
    {"phase": "repair", "model": "qwen2.5-vl-7b", "time_ms": 900, "sections_repaired": 1},
    {"phase": "validator_recheck", "model": "qwen2.5-vl-7b", "pass": true, "confidence": 0.91, "time_ms": 800}
  ],
  "total_time_ms": 3795,
  "vlm_calls": 3,
  "extraction_method": "mineru+vlm_repaired",
  "final_confidence": 0.91
}
```

---

## FastAPI Endpoints

**File**: `app/main.py`

| Method | Endpoint               | Description                          |
|--------|------------------------|--------------------------------------|
| POST   | `/ingest`              | Upload PDF, start ingestion job      |
| GET    | `/ingest/{job_id}`     | Get job status + progress            |
| GET    | `/ingest/{job_id}/pages/{n}` | Get extracted markdown for page |
| GET    | `/health`              | Health check (Qdrant, vLLM, MinerU)  |

Same API contract as Gemini plan — the consumer doesn't need to know which backend is running.

---

## Docker Compose

```yaml
# docker-compose.yml
services:
  # Primary document extraction
  mineru:
    image: opendatalab/mineru:latest
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    volumes:
      - ./data/pdfs:/data/pdfs
      - ./data/output:/data/output
    ports:
      - "8010:8010"                 # MinerU built-in FastAPI

  # VLM serving (Qwen2.5-VL-7B)
  vllm:
    image: vllm/vllm-openai:latest
    command: >
      --model Qwen/Qwen2.5-VL-7B-Instruct
      --tensor-parallel-size 1
      --max-model-len 32768
      --gpu-memory-utilization 0.90
      --enable-prefix-caching
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    ports:
      - "8000:8000"                 # OpenAI-compatible API
    volumes:
      - ./models:/root/.cache/huggingface

  # Vector database
  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - ./data/qdrant:/qdrant/storage

  # Application
  app:
    build: .
    depends_on:
      - mineru
      - vllm
      - qdrant
    environment:
      - VLLM_BASE_URL=http://vllm:8000/v1
      - MINERU_API_URL=http://mineru:8010
      - QDRANT_URL=http://qdrant:6333
      - BGE_DEVICE=cpu                # Or cuda:0 if GPU available
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data
```

---

## Implementation Order

| Step | Task                                                | Complexity |
|------|-----------------------------------------------------|------------|
| 1    | Project scaffolding + config + dependencies          | Low        |
| 2    | MinerU integration (Python API or HTTP client)       | Medium     |
| 3    | MinerU output parser → MinerUPageResult dataclass    | Medium     |
| 4    | PyMuPDF rasterizer for VLM input                     | Low        |
| 5    | vLLM client (OpenAI-compatible, async httpx)         | Low        |
| 6    | Document structure scan via Qwen2.5-VL-7B            | Medium     |
| 7    | Page classifier (which pages need VLM validation)    | Medium     |
| 8    | Validator agent + prompts                            | High       |
| 9    | Repair agent + merge repair into MinerU output       | High       |
| 10   | LangGraph wiring (validation flow)                   | Medium     |
| 11   | Cross-page merging + markdown output                 | Medium     |
| 12   | Section-aware recursive MD chunking + table JSON     | Medium     |
| 13   | BGE-M3 local embedding wrapper                       | Low        |
| 14   | Qdrant client + collection + indexes                 | Medium     |
| 15   | Pipeline orchestrator (end-to-end)                   | High       |
| 16   | FastAPI endpoints                                    | Medium     |
| 17   | Docker Compose (MinerU + vLLM + Qdrant + app)        | Medium     |
| 18   | Tests                                                | Medium     |

---

## Key Design Decisions

1. **MinerU as primary extractor, VLM as validator/repairer**: This inverts the Gemini plan where the VLM does everything. MinerU is a purpose-built document parser that scores 86-90+ on OmniDocBench. It handles layout, tables, formulas, OCR, cross-page merging natively. The VLM is expensive (GPU time) and only used where MinerU's output needs verification. This means ~60-80% of pages never touch the VLM.

2. **Qwen2.5-VL-7B over 72B as default**: For validation-only use (not primary extraction), the 7B model is sufficient. It scores 90+ on DocVQA, fits on a single A100 40GB, and is fast enough for the 20-40% of pages that need validation. 72B is available as an optional escalation path but requires 2-4× A100s.

3. **vLLM with OpenAI-compatible API**: Makes the VLM call interface identical to the Gemini plan's LangChain integration. Switching between on-premise and cloud is a config change (`base_url` + `model` name), not a code change.

4. **BGE-M3 over Gemini embeddings**: Multilingual, 1024-dim, supports dense+sparse+ColBERT retrieval, MIT license. No multimodal embedding (trade-off), but text-only embeddings with rich metadata in Qdrant payloads achieve the same retrieval quality for well report use cases.

5. **Selective VLM validation (not every page)**: The page classifier routes only complex/flagged/scanned pages to VLM validation. Text-heavy pages with high MinerU confidence skip VLM entirely. This keeps GPU utilization reasonable and throughput high.

6. **Repair vs re-extract**: When the validator finds gaps, the repair agent patches only the failing sections instead of re-extracting the full page. This is faster and preserves MinerU's correct portions.

7. **Docker Compose as deployment unit**: All four services (MinerU, vLLM, Qdrant, app) are containerized. `docker compose up` starts everything. No Kubernetes needed for single-node deployment.

8. **Same API contract as Gemini plan**: The FastAPI interface, Qdrant schema, and output formats are identical between plans. A downstream RAG system doesn't care which extraction backend was used.

9. **No multimodal embeddings**: The Gemini plan uses multimodal embeddings (text + image). On-premise, we compensate by: (a) storing VLM-generated image descriptions as text in chunks, (b) using rich metadata in Qdrant payloads for filtered retrieval, (c) BGE-M3's dense+sparse hybrid retrieval. For well reports (mostly text+tables), this is an acceptable trade-off.

10. **MinerU's cross-page table merging eliminates the hardest problem**: In the Gemini plan, we had to build cross-page table detection (DocStructure scan) + explicit merging in post-processing. MinerU does this out of the box. This removes significant complexity from our codebase.

---

## On-Premise vs Gemini Plan Comparison

| Aspect | Gemini Plan (v2) | On-Premise Plan | Trade-off |
|--------|-------------------|-----------------|-----------|
| Primary extractor | Gemini Flash 2.5 (VLM, every page) | MinerU 3.1 (purpose-built parser) | MinerU is deterministic & specialized; Gemini is more flexible |
| VLM usage | Every page (extractor + validator) | ~20-40% of pages (validation only) | 60-80% less GPU/API cost |
| Native PDF | Gemini Files API (native) | MinerU parses PDF natively | Both handle native PDF; no rasterization for extraction |
| Cross-page tables | DocStructure scan + manual merging | MinerU built-in | MinerU wins — zero code needed |
| Context caching | Gemini explicit cache (~4x savings) | vLLM prefix caching (automatic) | Gemini's is more explicit; vLLM's is transparent |
| Embeddings | gemini-embedding-2-preview (multimodal) | BGE-M3 (text-only, dense+sparse) | Lose multimodal embedding; gain hybrid retrieval |
| Cost model | Per-token API billing | Upfront GPU capex | On-premise wins at high volume |
| Quality ceiling | Gemini Pro 2.5 (~96 DocVQA) | Qwen2.5-VL-72B (~96.4 DocVQA) | Comparable at top tier |
| Deployment | API key + Docker (Qdrant) | Docker Compose (4 services) | More ops overhead on-premise |
| Maintenance | Google maintains models | You manage model updates, GPU drivers | More ownership burden |
| Cold start | Instant (API) | Model loading time (~30s vLLM) | Warm instances eliminate this |
| Scalability | Infinite (API) | Limited by GPU count | Add nodes for horizontal scale |
| Data privacy | Data leaves your network | **100% on-premise** | On-premise wins for sensitive data |
| Internet required | Yes (API calls) | **No** (fully air-gapped capable) | On-premise wins for restricted environments |

---

## Migration Path (Gemini → On-Premise)

If you build with the Gemini plan first and want to migrate later:

1. **Phase 0**: Replace Gemini Files API + Flash extractor → MinerU 3.1 `mineru_extractor.py`
2. **Phase 1**: Replace `gemini_client.py` structure scan → `vllm_client.py` + Qwen2.5-VL-7B
3. **Phase 2**: Add `page_classifier.py` (new — Gemini plan validates every page)
4. **Phase 3**: Replace Gemini extractor→validator graph → validator→repair graph
5. **Phase 4**: Replace `embeddings.py` from Gemini → BGE-M3
6. **Config**: Change `VLLM_BASE_URL` / model name in `.env`

The shared components that **don't change**: FastAPI endpoints, Qdrant schema, chunking, post-processing, logging, LangGraph state shape, Pydantic models.
