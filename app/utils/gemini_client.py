"""Shared Gemini API client with rate limiting and retry logic.

Encapsulates all Google GenAI SDK interactions behind a single interface
so the rest of the codebase never imports google.genai directly.
This makes future model swaps (e.g., to vLLM/Qwen) a config change.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from google import genai
from google.genai import types

from app.config import Settings, get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)


class GeminiClient:
    """Async-friendly wrapper around the Google GenAI SDK.

    Responsibilities:
    - API key management (single source)
    - Rate limiting via asyncio.Semaphore
    - Retry with exponential back-off on transient errors
    - Structured logging for every API call
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = genai.Client(api_key=self._settings.gemini_api_key)
        # Rate-limit concurrent Gemini calls (RPM headroom)
        self._semaphore = asyncio.Semaphore(self._settings.max_concurrent_batches)
        self._max_retries = 3
        self._base_delay = 1.0  # seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate(
        self,
        model: str,
        contents: list[Any],
        *,
        system_instruction: str | None = None,
        response_mime_type: str | None = None,
        cached_content: str | None = None,
        temperature: float = 0.1,
    ) -> str:
        """Send a generation request with rate limiting and retry.

        Args:
            model: Model resource name, e.g. "models/gemini-2.5-flash".
            contents: List of content parts (text, file references, etc.).
            system_instruction: Optional system prompt.
            response_mime_type: E.g. "application/json" for structured output.
            cached_content: Cached content resource name for cost savings.
            temperature: Sampling temperature.

        Returns:
            The generated text response.
        """
        config = types.GenerateContentConfig(
            temperature=temperature,
            system_instruction=system_instruction,
            response_mime_type=response_mime_type,
        )

        if cached_content:
            config.cached_content = cached_content

        return await self._call_with_retry(model, contents, config)

    async def upload_file(self, file_path: str, mime_type: str = "application/pdf") -> Any:
        """Upload a file to the Gemini Files API.

        Returns:
            The uploaded file object (contains name/uri for referencing).
        """
        logger.info("uploading_file", path=file_path, mime_type=mime_type)

        def _upload() -> Any:
            return self._client.files.upload(
                file=file_path,
                config=types.UploadFileConfig(mime_type=mime_type),
            )

        return await asyncio.to_thread(_upload)

    async def create_cache(
        self,
        model: str,
        *,
        system_instruction: str,
        contents: list[Any],
        ttl_seconds: int = 3600,
        display_name: str = "",
    ) -> Any:
        """Create an explicit content cache for cost savings.

        Returns:
            The cached content object (contains name for referencing).
        """
        logger.info("creating_cache", model=model, display_name=display_name, ttl=ttl_seconds)

        def _create() -> Any:
            return self._client.caches.create(
                model=model,
                config=types.CreateCachedContentConfig(
                    display_name=display_name,
                    system_instruction=system_instruction,
                    contents=contents,
                    ttl=f"{ttl_seconds}s",
                ),
            )

        return await asyncio.to_thread(_create)

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts.

        Returns:
            List of embedding vectors.
        """

        def _embed() -> list[list[float]]:
            result = self._client.models.embed_content(
                model=model,
                contents=texts,
            )
            return [e.values for e in result.embeddings]

        return await asyncio.to_thread(_embed)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _call_with_retry(
        self,
        model: str,
        contents: list[Any],
        config: types.GenerateContentConfig,
    ) -> str:
        """Execute a generate call with semaphore, retry, and logging."""
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            async with self._semaphore:
                start = time.perf_counter()
                try:
                    response = await asyncio.to_thread(
                        self._client.models.generate_content,
                        model=model,
                        contents=contents,
                        config=config,
                    )
                    elapsed_ms = int((time.perf_counter() - start) * 1000)
                    text = response.text or ""
                    logger.info(
                        "gemini_call_ok",
                        model=model,
                        attempt=attempt,
                        time_ms=elapsed_ms,
                        output_chars=len(text),
                    )
                    return text

                except Exception as exc:
                    elapsed_ms = int((time.perf_counter() - start) * 1000)
                    last_error = exc
                    logger.warning(
                        "gemini_call_error",
                        model=model,
                        attempt=attempt,
                        time_ms=elapsed_ms,
                        error=str(exc),
                    )
                    if attempt < self._max_retries:
                        delay = self._base_delay * (2 ** (attempt - 1))
                        await asyncio.sleep(delay)

        raise RuntimeError(
            f"Gemini API call failed after {self._max_retries} attempts"
        ) from last_error
