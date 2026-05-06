"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routes.chat import router as chat_router
from app.routes.ingest import router as ingest_router
from app.routes.search import router as search_router
from app.utils.gemini_client import GeminiClient
from app.utils.logging import get_logger, setup_logging
from app.vectorstore.embedder import GeminiEmbedder
from app.vectorstore.qdrant_store import QdrantVectorStore

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application startup / shutdown lifecycle.

    Constructs process-wide singletons for the Gemini client, embedder,
    and Qdrant store. Sharing these across all requests is what enforces
    the global concurrency cap (one ``Semaphore(max_concurrent_pages)``
    inside ``GeminiClient`` instead of one per request).
    """
    settings = get_settings()
    setup_logging(log_level=settings.log_level, log_format=settings.log_format)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "app_started",
        max_concurrent_pages=settings.max_concurrent_pages,
        render_dpi=settings.render_dpi,
        pass_confidence_threshold=settings.pass_confidence_threshold,
        gemini_configured=bool(settings.gemini_api_key),
    )

    _app.state.settings = settings
    _app.state.gemini = GeminiClient(settings) if settings.gemini_api_key else None
    _app.state.embedder = GeminiEmbedder(settings) if settings.gemini_api_key else None
    _app.state.qdrant = QdrantVectorStore(settings)

    try:
        await _app.state.qdrant.ensure_collection()
        logger.info("qdrant_ready", collection=settings.qdrant_collection)
    except Exception:
        logger.warning("qdrant_init_failed", exc_info=True)

    try:
        yield
    finally:
        try:
            await _app.state.qdrant.close()
        except Exception:
            logger.warning("qdrant_close_failed", exc_info=True)
        logger.info("app_stopped")


tags_metadata = [
    {
        "name": "ingest",
        "description": (
            "Document Intelligence pipeline (L1). Upload PDFs, run the streaming "
            "per-page extraction graph, and serve grounded evidence (page PNGs, "
            "bbox crops, tables, entities, traces)."
        ),
    },
    {
        "name": "search",
        "description": (
            "Semantic search over ingested document chunks. "
            "Embed a query with Gemini text-embedding-004 and retrieve "
            "the most relevant passages from Qdrant."
        ),
    },
    {
        "name": "chat",
        "description": (
            "RAG agent chat — ask questions about ingested documents. "
            "The agent searches, reasons, and synthesizes grounded answers "
            "with source citations."
        ),
    },
    {"name": "system", "description": "Health, root, and service metadata."},
]


app = FastAPI(
    title="WellSynth AI",
    description=(
        "Well Operations Intelligence — Document Intelligence layer (L1). "
        "Per-page rasterized extraction with Gemini 3 Flash Preview self-validation + Gemini 3.1 Pro Preview escalation "
        "on flagged gaps, with bbox grounding for every table, image, and entity."
    ),
    version="0.6.0",
    lifespan=lifespan,
    openapi_tags=tags_metadata,
)


# CORS — permissive for pilot; tighten in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(ingest_router)
app.include_router(search_router)
app.include_router(chat_router)


# Static UI — minimal chat frontend served at /ui
from pathlib import Path as _Path
_static_dir = _Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

    @app.get("/ui", tags=["system"], include_in_schema=False)
    async def chat_ui() -> FileResponse:
        return FileResponse(_static_dir / "chat.html")

    @app.get("/ingest-ui", tags=["system"], include_in_schema=False)
    async def ingest_ui() -> FileResponse:
        return FileResponse(_static_dir / "ingest.html")


# ---------------------------------------------------------------------------
# System endpoints
# ---------------------------------------------------------------------------


@app.get("/", tags=["system"], summary="Service root + capability summary")
async def root() -> dict:
    settings = get_settings()
    return {
        "service": "WellSynth AI",
        "version": app.version,
        "docs": "/docs",
        "capability_layers": {
            "L1_document_intelligence": "available",
            "L2_domain_knowledge": "available",
            "L3_tenant_hierarchy": "planned",
            "L4_artifact_linkage": "planned",
            "L5_retrieval_backbone": "available",
            "L6_synthesis_agents": "available",
            "L7_rbac_governance": "planned",
            "L8_workspace_ui": "planned",
        },
        "config": {
            "render_dpi": settings.render_dpi,
            "max_concurrent_pages": settings.max_concurrent_pages,
            "pass_confidence_threshold": settings.pass_confidence_threshold,
            "extraction_model": settings.extraction_model,
            "escalation_model": settings.escalation_model,
            "gemini_configured": bool(settings.gemini_api_key),
        },
    }


@app.get("/health", tags=["system"], summary="Liveness check")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Global error handler
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled_exception", error=str(exc), error_type=type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error_type": type(exc).__name__},
    )
