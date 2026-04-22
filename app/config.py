"""Application configuration via environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration — loaded from .env or environment variables.

    Single source of truth for all runtime settings.  Follows the
    twelve-factor app principle: config lives in the environment.
    """

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # --- Gemini API ---
    gemini_api_key: str = ""

    # --- Qdrant ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "wellsynthai_documents"

    # --- Pipeline ---
    max_concurrent_batches: int = 25
    batch_size: int = 5
    media_resolution_default: str = "MEDIUM"
    max_retries: int = 2

    # --- PDF Limits (Gemini Files API) ---
    pdf_max_pages: int = 1000       # Hard limit per upload
    pdf_max_size_mb: int = 50       # Hard limit per upload
    pdf_split_target_pages: int = 800  # Target chunk size (headroom below max)
    pdf_split_min_pages: int = 50      # Minimum chunk to avoid tiny fragments

    # --- Models ---
    extraction_model: str = "models/gemini-2.5-flash"
    escalation_model: str = "models/gemini-2.5-pro"
    embedding_model: str = "models/gemini-embedding-exp-03-07"

    # --- Storage ---
    output_dir: Path = Path("./data/output")
    upload_dir: Path = Path("./data/uploads")

    # --- Logging ---
    log_level: str = "INFO"
    log_format: str = "json"  # "json" | "console"


def get_settings() -> Settings:
    """Factory that returns a cached Settings instance."""
    return Settings()
