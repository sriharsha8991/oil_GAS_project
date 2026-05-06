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


# Substrings used to identify retryable transient errors. Anything else
# (auth, bad request, malformed prompt) raises immediately so we don't
# burn 3x quota on misconfiguration.
_RETRYABLE_HINTS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "deadline",
    "timeout",
    "timed out",
    "unavailable",
    "reset by peer",
    "connection",
    "resource exhausted",
)
_NON_RETRYABLE_HINTS = (
    "401",
    "403",
    "permission_denied",
    "unauthenticated",
    "invalid_argument",
    "invalid argument",
    "not_found",
    "failed_precondition",
)


def _is_retryable(exc: BaseException) -> bool:
    """Classify a Gemini exception as retryable (transient) or fatal."""
    if isinstance(exc, asyncio.TimeoutError):
        return True
    msg = str(exc).lower()
    if any(h in msg for h in _NON_RETRYABLE_HINTS):
        return False
    if any(h in msg for h in _RETRYABLE_HINTS):
        return True
    # Default: retry once for unknown exceptions but don't burn full budget.
    return True


def _extract_usage(response: Any, model: str) -> dict[str, Any]:
    """Pull token counts out of a Gemini response. Always returns ints."""
    meta = getattr(response, "usage_metadata", None)
    in_tok = int(getattr(meta, "prompt_token_count", 0) or 0) if meta else 0
    out_tok = int(getattr(meta, "candidates_token_count", 0) or 0) if meta else 0
    total = int(getattr(meta, "total_token_count", 0) or 0) if meta else (in_tok + out_tok)
    return {
        "model": model,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": total,
    }


class GeminiClient:
    """Async-friendly wrapper around the Google GenAI SDK (singleton).

    Responsibilities:
    - One `genai.Client` instance shared by all 100 workers
    - Concurrency cap via `asyncio.Semaphore(max_concurrent_pages)`
    - Retry with exponential back-off on transient errors
    - Per-call timeout to prevent hung calls from holding semaphore slots
    - Structured logging for every API call
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = genai.Client(api_key=self._settings.gemini_api_key)
        self._semaphore = asyncio.Semaphore(self._settings.max_concurrent_pages)
        self._max_retries = 3
        self._base_delay = 1.0  # seconds
        self._call_timeout = self._settings.gemini_call_timeout_seconds

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
    ) -> tuple[str, dict[str, Any]]:
        """Send an inline image + text prompt to Gemini.

        Returns:
            (text, usage) where usage = {"model": str, "input_tokens": int,
            "output_tokens": int, "total_tokens": int}.
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
    ) -> tuple[str, dict[str, Any]]:
        """Execute generate_content with semaphore, timeout, retry, and logging."""
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            async with self._semaphore:
                start = time.perf_counter()
                try:
                    response = await asyncio.wait_for(
                        self._client.aio.models.generate_content(
                            model=model,
                            contents=contents,
                            config=config,
                        ),
                        timeout=self._call_timeout,
                    )
                    elapsed_ms = int((time.perf_counter() - start) * 1000)
                    text = response.text or ""
                    usage = _extract_usage(response, model)
                    logger.info(
                        "gemini_call_ok",
                        model=model,
                        attempt=attempt,
                        time_ms=elapsed_ms,
                        output_chars=len(text),
                        input_tokens=usage["input_tokens"],
                        output_tokens=usage["output_tokens"],
                        total_tokens=usage["total_tokens"],
                    )
                    return text, usage

                except Exception as exc:
                    elapsed_ms = int((time.perf_counter() - start) * 1000)
                    last_error = exc
                    retryable = _is_retryable(exc)
                    logger.warning(
                        "gemini_call_error",
                        model=model,
                        attempt=attempt,
                        time_ms=elapsed_ms,
                        retryable=retryable,
                        error=str(exc),
                    )
                    if not retryable:
                        raise
                    if attempt < self._max_retries:
                        delay = self._base_delay * (2 ** (attempt - 1))
                        await asyncio.sleep(delay)

        raise RuntimeError(
            f"Gemini API call failed after {self._max_retries} attempts"
        ) from last_error
