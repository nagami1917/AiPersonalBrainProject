"""OpenAI embeddings adapter.

Batching, retry, and dimension enforcement live here so that nothing upstream
has to think about provider failure modes. `dim` is checked against the pinned
value on every response: a provider that silently changed dimension would
otherwise poison the index rather than fail loudly.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import ProviderUnavailable

log = logging.getLogger(__name__)


class OpenAIEmbedder:
    """Batches, retries on 429/5xx, and never partially writes.

    A partial embed would leave a document with an incomplete index that still
    reports `ready` — worse than a clean failure that retries.
    """

    def __init__(self, settings: Settings, *, batch_size: int = 96, max_retries: int = 4) -> None:
        self._settings = settings
        self.model = settings.pinned_embedding_model
        self.dim = settings.pinned_embedding_dim
        self._batch_size = batch_size
        self._max_retries = max_retries

    async def _embed_batch(
        self, client: httpx.AsyncClient, batch: Sequence[str]
    ) -> list[list[float]]:
        delay = 1.0
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = await client.post(
                    f"{self._settings.openai_base_url}/embeddings",
                    headers={"Authorization": f"Bearer {self._settings.openai_api_key}"},
                    json={
                        "model": self.model,
                        "input": list(batch),
                        "dimensions": self.dim,
                    },
                    timeout=60,
                )
                if response.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(
                        f"retryable {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                data: list[dict[str, Any]] = response.json()["data"]
                vectors = [item["embedding"] for item in data]
                for vector in vectors:
                    if len(vector) != self.dim:
                        raise ProviderUnavailable(
                            f"Embedding dimension {len(vector)} != pinned {self.dim}",
                            code="embedding_dim_mismatch",
                        )
                return vectors
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                last_error = exc
                log.warning("embedding batch failed (attempt %s): %s", attempt + 1, exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 20)
        raise ProviderUnavailable(f"Embedding provider failed: {last_error}")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        async with httpx.AsyncClient() as client:
            results: list[list[float]] = []
            for start in range(0, len(texts), self._batch_size):
                batch = texts[start : start + self._batch_size]
                results.extend(await self._embed_batch(client, batch))
            return results
