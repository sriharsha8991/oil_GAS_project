"""Singleton Gemini client for inline image + text generation.

v3: drops Files API + caching entirely. Every call sends inline image
bytes (`types.Part.from_bytes`) plus a text prompt. A single shared
`asyncio.Semaphore(max_concurrent_pages)` guards all calls so the
worker pool can never exceed the configured concurrency.
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
    """Async-friendly wrapper around the Google GenAI SDK (singleton).

    Responsibilities:
    - One `genai.Client` instance shared by all 100 workers
    - Concurrency cap via `asyncio.Semaphore(max_concurrent_pages)`
    - Retry with exponential back-off on transient errors
    - Structured logging for every API call
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = genai.Client(api_key=self._settings.gemini_api_key)
        self._semaphore = asyncio.Semaphore(self._settings.max_concurrent_pages)
        self._max_retries = 3
        self._base_delay = 1.0  # seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_with_image(
        self,
        model: str,
        prompt: str,
        png_bytes: bytes,
        *,
        system_instruction: str | None = None,
        response_mime_type: str | None = "application/json",
        temperature: float = 0.1,
        mime_type: str = "image/png",
    ) -> str:
        """Send an inline image + text prompt to Gemini.

        Args:
            model: Model resource name, e.g. "models/gemini-3-flash-preview".
            prompt: User text prompt.
            png_bytes: Raw PNG bytes of the rendered page.
            system_instruction: Optional system prompt.
            response_mime_type: Default "application/json" for structured output.
            temperature: Sampling temperature.
            mime_type: Image mime type (default image/png).

        Returns:
            The generated text response.
        """
        image_part = types.Part.from_bytes(data=png_bytes, mime_type=mime_type)
        contents = [image_part, prompt]

        config = types.GenerateContentConfig(
            temperature=temperature,
            system_instruction=system_instruction,
            response_mime_type=response_mime_type,
        )

        return await self._call_with_retry(model, contents, config)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _call_with_retry(
        self,
        model: str,
        contents: list[Any],
        config: types.GenerateContentConfig,
    ) -> str:
        """Execute generate_content with semaphore, retry, and logging."""
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            async with self._semaphore:
                start = time.perf_counter()
                try:
                    response = await self._client.aio.models.generate_content(
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
