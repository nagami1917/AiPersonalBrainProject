from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Tests must never reach a real provider or a real project.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SUPABASE_URL", "http://127.0.0.1:54321")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-with-at-least-32-characters")
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
os.environ.setdefault("OPENAI_API_KEY", "test-key")


class FakeEmbedder:
    """Hashed bag-of-words "embedding": deterministic, offline, and — crucially —
    semantically ordered.

    Cosine similarity of these vectors tracks lexical overlap, so retrieval
    tests behave like the real thing: a question sharing vocabulary with a chunk
    scores high, an unrelated query scores near zero and is gated out by
    min_similarity. It is NOT a model quality test; it is a pipeline test, and
    the distinction is deliberate.
    """

    def __init__(self, dim: int = 1536) -> None:
        self.model = "text-embedding-3-small"
        self.dim = dim
        self.calls: list[list[str]] = []

    async def embed(self, texts):  # type: ignore[no-untyped-def]
        self.calls.append(list(texts))
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        tokens = [t for t in _tokenize(text) if len(t) > 2]
        for token in tokens:
            vector[_stable_hash(token) % self.dim] += 1.0
        norm = sum(v * v for v in vector) ** 0.5
        if norm == 0:
            # Orthogonal-ish fallback so unrelated text scores ~0, not 1.
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]


def _tokenize(text: str) -> list[str]:
    cleaned = "".join(c.lower() if c.isalnum() else " " for c in text)
    return cleaned.split()


def _stable_hash(text: str) -> int:
    """Stable across runs, unlike builtin hash() which is salted per process."""
    value = 2166136261
    for char in text:
        value = (value ^ ord(char)) * 16777619 & 0xFFFFFFFF
    return value


class FakeLLM:
    """Records prompts so tests can assert the model was (or was not) called."""

    def __init__(self, reply: str = "Answer with a citation [1].") -> None:
        self.model = "test-llm"
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.reply

    async def stream(self, system: str, user: str):  # type: ignore[no-untyped-def]
        self.calls.append((system, user))
        for token in self.reply.split(" "):
            yield token + " "


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()
