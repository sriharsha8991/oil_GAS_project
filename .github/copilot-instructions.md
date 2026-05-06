---
applyTo: "**"
---
# Wellsynthai — Project Guardrails

## North Star
A user uploads a PDF → ingestion produces page-level markdown + images + tables + entities → retrieval surfaces the right chunks (text + image refs) → the RAG agent answers from them with citations.

**Anything that does not directly serve this loop is out of scope until further notice.**

## Hard Rules

- **No new dependencies.** Do not add packages to `requirements.txt` / `pyproject.toml` without explicit approval.
- **No new endpoints, agents, or config flags** unless the user asks for one by name.
- **No speculative features** — no audio/video, alternate OCR engines, agentic browsing, multi-tenant logic, auth layers, analytics, etc.
- **Prefer deletion over addition.** When two pieces of code do the same job, merge to one; do not leave shims or "legacy" wrappers.
- **Don't beautify code you aren't changing.** No drive-by docstrings, type hints, comments, renames, or reformatting.
- **Don't add error handling for things that can't happen.** Validate at system boundaries (`/ingest`, `/chat`) only.

## Stack Facts (do not re-derive)

- FastAPI service in `app/`, Celery worker in `app/workers/`, Redis for state, Qdrant for vectors, Gemini (Flash 2.5 + Pro 2.5) for multimodal extraction.
- Ingestion is **per-page streaming** with ~100 concurrent workers (see `PLAN_v3_ingestion.md`). Do not propose batching.
- Page rendering: `pymupdf` `page.get_pixmap(dpi=200).tobytes("png")`, sent inline to Gemini — **no Files API, no caching layer**.
- Extraction loop per page: `extractor → validator → (loop ≤3) → escalator`. Do not add new stages.
- Outputs land in `data/output/<doc>/` as `page_NNN.md`, `pages_png/`, `tables/`, `entities.json`, `full_document.md`.

## When in Doubt

1. Trace the live request path before editing: `POST /ingest → orchestrator → page_streamer → extractor/validator/escalator → postprocessor → indexers` and `POST /chat → rag_agent → rag_tools → reranker → answer`.
2. If a function is not on that path and not called by something on that path, it is a deletion candidate — flag it, don't extend it.
3. If a question is "should I add X?", the default answer is **no**.
