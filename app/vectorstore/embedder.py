"""Gemini text embedding client.

Uses ``text-embedding-004`` (768-dim) via the google-genai async SDK.
Supports batching up to 100 texts per call.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from google import genai
from google.genai import types

from app.utils.logging import get_logger

if TYPE_CHECKING:
    from app.config import Settings

logger = get_logger(__name__)


class GeminiEmbedder:
    """Async wrapper for Gemini embedding API."""

    def __init__(self, settings: "Settings") -> None:
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.embedding_model
        self._batch_size = settings.embedding_batch_size
        self._dimensions = settings.embedding_dimensions
        self._semaphore = asyncio.Semaphore(10)  # max concurrent embed calls

    async def embed_texts(
        self,
        texts: list[str],
        *,
        task_type: str = "RETRIEVAL_DOCUMENT",
    ) -> list[list[float]]:
        """Embed a list of texts, batching as needed.

        Args:
            texts: Strings to embed.
            task_type: ``RETRIEVAL_DOCUMENT`` for indexing,
                       ``RETRIEVAL_QUERY`` for search queries.

        Returns:
            List of embedding vectors (each a list of floats).
        """
        if not texts:
            return []

        all_embeddings: list[list[float]] = []

        # Split into batches
        batches = [
            texts[i : i + self._batch_size]
            for i in range(0, len(texts), self._batch_size)
        ]

        for batch in batches:
            embeddings = await self._embed_batch(batch, task_type=task_type)
            all_embeddings.extend(embeddings)

        return all_embeddings

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single search query."""
        result = await self.embed_texts([text], task_type="RETRIEVAL_QUERY")
        return result[0]

    async def _embed_batch(
        self,
        texts: list[str],
        *,
        task_type: str,
    ) -> list[list[float]]:
        """Call the Gemini embed_content API for a single batch."""
        async with self._semaphore:
            try:
                response = await self._client.aio.models.embed_content(
                    model=self._model,
                    contents=texts,
                    config=types.EmbedContentConfig(
                        task_type=task_type,
                        output_dimensionality=self._dimensions,
                    ),
                )
                logger.debug(
                    "embed_batch_done",
                    batch_size=len(texts),
                    model=self._model,
                )
                return [e.values for e in response.embeddings]
            except Exception:
                logger.error(
                    "embed_batch_failed",
                    batch_size=len(texts),
                    exc_info=True,
                )
                raise
