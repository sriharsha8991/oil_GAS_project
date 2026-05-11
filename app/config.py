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
    max_concurrent_pages: int = 150   # Per-process Gemini in-flight cap; cluster ceiling = this × celery_concurrency (Tier 3: 20K RPM Flash)
    render_dpi: int = 200             # PNG rasterization DPI (200 default, 300 for small print)
    pass_confidence_threshold: float = 0.85  # Below this OR with gaps -> escalate to Pro

    # --- Resilience ---
    gemini_call_timeout_seconds: float = 120.0   # Per-call hard timeout for Gemini
    embed_call_timeout_seconds: float = 60.0     # Per-call hard timeout for embeddings
    max_upload_mb: int = 500                     # Reject uploads larger than this

    # --- Models ---
    extraction_model: str = "models/gemini-3-flash-preview"   # iter 1 & 2
    escalation_model: str = "models/gemini-3.1-pro-preview"           # iter 3 (FINAL)

    # --- Chat models (cheap, fast — context already curated by retrieval) ---
    # NOTE: A Flash-Lite variant of Gemini 3 Preview is not yet available
    # via API (returns 404 as of 2026-05). When it ships, override these
    # via env to drop chat-path TTFT another ~30%.
    chat_triage_model: str = "models/gemini-3-flash-preview"    # LLM 0: route + meta-answer
    chat_planner_model: str = "models/gemini-3-flash-preview"   # LLM 1: query plan
    chat_synth_model: str = "models/gemini-3-flash-preview"     # LLM 2: grounded answer

    # --- Pricing (USD per 1M tokens, approximate; override via env) ---
    # Used purely for ingestion cost estimation in job summaries.
    flash_input_usd_per_1m: float = 0.30
    flash_output_usd_per_1m: float = 2.50
    pro_input_usd_per_1m: float = 1.25
    pro_output_usd_per_1m: float = 10.00
    embedding_usd_per_1m: float = 0.15

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
    upload_dir: Path = Path("./data/uploads")  # transient PDF landing zone
    signed_url_ttl_seconds: int = 60           # short TTL for image-serving redirects

    # --- Supabase (PR-5: auth + Postgres; PR-6: Storage; PR-7: Realtime) ---
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwks_url: str = ""
    database_url: str = ""
    worker_database_url: str = ""

    # --- Email (Resend) — used to mail generated passwords on invite ---
    resend_api_key: str = ""
    resend_from_email: str = "no-reply@rax-resume.xyz"
    resend_from_name: str = "Wellsynth"
    # Optional sign-in URL embedded as a CTA button in invite/reset emails.
    # Leave blank to render the email without a button.
    sign_in_url: str = ""

    # --- Support tickets (POST /support/tickets etc.) ---
    support_inbox_email: str = "srih8991@gmail.com"
    support_from_email: str = ""             # falls back to resend_from_email when blank
    support_notify_user_on_update: bool = True
    support_rate_limit_per_hour: int = 5
    support_rate_limit_per_day: int = 20
    support_max_attachments: int = 3
    support_max_attachment_bytes: int = 5 * 1024 * 1024
    support_attachment_ttl_seconds: int = 7 * 24 * 3600  # 7 days
    # FE base URL used when composing links inside support emails (e.g.
    # "{frontend_base_url}/help/tickets/{id}"). Optional.
    frontend_base_url: str = ""

    # --- CORS ---
    cors_allow_origins: str = "http://localhost:3000"  # comma-separated

    # --- Logging ---
    log_level: str = "INFO"
    log_format: str = "json"  # "json" | "console"


def get_settings() -> Settings:
    """Factory that returns a cached Settings instance."""
    return Settings()
