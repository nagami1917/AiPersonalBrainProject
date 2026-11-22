"""Provider adapters: embeddings and chat completions.

Kept behind a narrow interface so tests can stub them without network access,
and so a provider swap never touches retrieval or grounding logic.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol

import httpx

from app.core.config import Settings
from app.core.errors import ProviderUnavailable

log = logging.getLogger(__name__)


class Embedder(Protocol):
    model: str
    dim: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class LLM(Protocol):
    model: str

    async def complete(self, system: str, user: str) -> str: ...

    def stream(self, system: str, user: str) -> AsyncIterator[str]: ...


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


class OpenAIChat:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.model = settings.llm_model

    async def complete(self, system: str, user: str) -> str:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._settings.openai_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._settings.openai_api_key}"},
                json={
                    "model": self.model,
                    "temperature": self._settings.llm_temperature,
                    "max_tokens": self._settings.llm_max_output_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=90,
            )
            if response.status_code >= 500 or response.status_code == 429:
                raise ProviderUnavailable("LLM provider unavailable")
            response.raise_for_status()
            body: dict[str, Any] = response.json()
            content: str = body["choices"][0]["message"]["content"]
            return content

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        import json

        client = httpx.AsyncClient(timeout=120)
        async with client:
            request = client.build_request(
                "POST",
                f"{self._settings.openai_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._settings.openai_api_key}"},
                json={
                    "model": self.model,
                    "temperature": self._settings.llm_temperature,
                    "max_tokens": self._settings.llm_max_output_tokens,
                    "stream": True,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            response = await client.send(request, stream=True)
            try:
                if response.status_code >= 400:
                    raise ProviderUnavailable("LLM provider unavailable")
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except ValueError:
                        continue
                    delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                    if delta:
                        yield delta
            finally:
                await response.aclose()


def build_embedder(settings: Settings) -> Embedder:
    return OpenAIEmbedder(settings)


def build_llm(settings: Settings) -> LLM:
    return OpenAIChat(settings)
