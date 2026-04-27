"""Application configuration via environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration — loaded from .env or environment variables.

    Single source of truth for all runtime settings.  Follows the
    twelve-factor app principle: config lives in the environment.
    """

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # --- Gemini API ---
    gemini_api_key: str = ""

    # --- Pipeline (v3: streaming per-page) ---
    max_concurrent_pages: int = 200   # Worker pool size; one Gemini call per worker
    render_dpi: int = 200             # PNG rasterization DPI (200 default, 300 for small print)
    max_iterations: int = 3           # Hard cap: iter1 Flash, iter2 Flash, iter3 Pro (FINAL)
    pass_confidence_threshold: float = 0.85

    # --- Models ---
    extraction_model: str = "models/gemini-3-flash-preview"   # iter 1 & 2
    escalation_model: str = "models/gemini-3.1-pro-preview"           # iter 3 (FINAL)

    # --- Qdrant ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "wellsynthai_documents"

    # --- Embedding ---
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768
    embedding_batch_size: int = 100  # max texts per embed_content call

    # --- Chunking ---
    chunk_size: int = 1000       # chars per chunk
    chunk_overlap: int = 200     # overlap between consecutive chunks

    # --- Storage ---
    output_dir: Path = Path("./data/output")
    upload_dir: Path = Path("./data/uploads")

    # --- Logging ---
    log_level: str = "INFO"
    log_format: str = "json"  # "json" | "console"


def get_settings() -> Settings:
    """Factory that returns a cached Settings instance."""
    return Settings()
