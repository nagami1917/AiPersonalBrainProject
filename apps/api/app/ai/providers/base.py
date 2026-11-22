"""The two capabilities the AI layer needs, and nothing else.

These Protocols are the seam between product logic and vendors. Everything
downstream — retrieval, grounding, ingestion — depends on these shapes, never on
OpenAI. That is what makes the test suite able to run with no network: the
fakes in conftest.py implement exactly this file and nothing more.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol


class Embedder(Protocol):
    """Turns text into vectors. `model` and `dim` are pinned, not per-request."""

    model: str
    dim: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class LLM(Protocol):
    """Chat completion, blocking or streamed."""

    model: str

    async def complete(self, system: str, user: str) -> str: ...

    def stream(self, system: str, user: str) -> AsyncIterator[str]: ...
