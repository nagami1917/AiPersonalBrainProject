"""RAG: context in, a verifiable answer out.

Three stages, mirroring the design's split:

  * `context_builder` — retrieved chunks → numbered, delimited `<source>` blocks.
  * `prompt_builder`  — what the model is instructed to do with them.
  * `answer_generator` — what the model's output is *allowed* to claim.

The order matters in one non-obvious way: the refusal decision (`should_refuse`)
belongs to answer generation, but must run before the prompt is ever sent. The
caller in `services/chat_service.py` does exactly that, and the test suite asserts
it by checking the fake LLM recorded zero calls.
"""

from __future__ import annotations

from app.ai.rag.answer_generator import (
    REFUSAL_TEMPLATE,
    Citation,
    GroundedAnswer,
    resolve_citations,
    should_refuse,
)
from app.ai.rag.context_builder import Source, build_context
from app.ai.rag.prompt_builder import HISTORY_TURNS, SYSTEM_PROMPT, build_user_turn

__all__ = [
    "HISTORY_TURNS",
    "REFUSAL_TEMPLATE",
    "SYSTEM_PROMPT",
    "Citation",
    "GroundedAnswer",
    "Source",
    "build_context",
    "build_user_turn",
    "resolve_citations",
    "should_refuse",
]
