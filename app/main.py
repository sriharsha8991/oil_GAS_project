"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.config import get_settings
from app.routes.ingest import router as ingest_router
from app.utils.logging import setup_logging


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application startup / shutdown lifecycle."""
    settings = get_settings()
    setup_logging(log_level=settings.log_level, log_format=settings.log_format)
    # Ensure output and upload dirs exist
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="Wellsynthai",
    description="PDF ingestion pipeline for well report documents",
    version="0.1.0",
    lifespan=lifespan,
)

# --- Routers ---
app.include_router(ingest_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Basic health check endpoint."""
    return {"status": "ok"}
