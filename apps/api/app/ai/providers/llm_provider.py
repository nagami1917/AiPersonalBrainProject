"""OpenAI chat adapter.

Both the blocking and streaming paths send the same request shape; the only
difference is whether the response is consumed whole or as SSE deltas.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import ProviderUnavailable


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
