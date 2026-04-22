"""Gemini Files API upload and context caching helpers.

Handles the lifecycle: upload PDF → create cache → return cache reference.
The cache is reused across all extractor/validator calls for a document.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.utils.gemini_client import GeminiClient
from app.utils.logging import get_logger

logger = get_logger(__name__)


class PDFUploader:
    """Upload a PDF to Gemini Files API and manage its explicit cache.

    Usage:
        uploader = PDFUploader(gemini_client)
        upload_result = await uploader.upload_and_cache(
            pdf_path=Path("report.pdf"),
            model="models/gemini-2.5-flash",
            system_instruction="You are a document extraction agent...",
            doc_id="abc-123",
        )
        # upload_result.file  → the uploaded file reference
        # upload_result.cache → the cached content reference
    """

    def __init__(self, client: GeminiClient) -> None:
        self._client = client

    async def upload_and_cache(
        self,
        pdf_path: Path,
        model: str,
        system_instruction: str,
        doc_id: str,
        ttl_seconds: int = 3600,
    ) -> UploadResult:
        """Upload a PDF and create an explicit cache.

        Args:
            pdf_path: Path to the PDF file on disk.
            model: Model to cache against (e.g. "models/gemini-2.5-flash").
            system_instruction: System prompt to bake into the cache.
            doc_id: Unique document identifier for cache naming.
            ttl_seconds: Cache time-to-live (default 1 hour).

        Returns:
            UploadResult with both file and cache references.
        """
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        file_size_mb = pdf_path.stat().st_size / (1024 * 1024)
        if file_size_mb > 50:
            raise ValueError(f"PDF exceeds 50MB limit: {file_size_mb:.1f}MB")

        logger.info("upload_and_cache_start", doc_id=doc_id, file_size_mb=round(file_size_mb, 2))

        # Step 1: Upload the file
        uploaded_file = await self._client.upload_file(str(pdf_path))
        logger.info("file_uploaded", doc_id=doc_id, file_name=uploaded_file.name)

        # Step 2: Create explicit cache
        cache = await self._client.create_cache(
            model=model,
            system_instruction=system_instruction,
            contents=[uploaded_file],
            ttl_seconds=ttl_seconds,
            display_name=f"wellsynthai_{doc_id}",
        )
        logger.info("cache_created", doc_id=doc_id, cache_name=cache.name)

        return UploadResult(file=uploaded_file, cache=cache)


class UploadResult:
    """Container for an uploaded file and its associated cache."""

    def __init__(self, file: Any, cache: Any) -> None:
        self.file = file
        self.cache = cache

    @property
    def file_name(self) -> str:
        return self.file.name

    @property
    def cache_name(self) -> str:
        return self.cache.name
