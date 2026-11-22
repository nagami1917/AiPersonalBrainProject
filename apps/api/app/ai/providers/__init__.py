"""Provider implementations, selected in one place.

Importing from this module (never from a concrete adapter) is what keeps a vendor
swap to a single file. Tests substitute the Protocols in `base.py`.
"""

from __future__ import annotations

from app.ai.providers.base import LLM, Embedder
from app.ai.providers.embedding_provider import OpenAIEmbedder
from app.ai.providers.llm_provider import OpenAIChat
from app.core.config import Settings

__all__ = ["Embedder", "LLM", "OpenAIEmbedder", "OpenAIChat", "build_embedder", "build_llm"]


def build_embedder(settings: Settings) -> Embedder:
    return OpenAIEmbedder(settings)


def build_llm(settings: Settings) -> LLM:
    return OpenAIChat(settings)
