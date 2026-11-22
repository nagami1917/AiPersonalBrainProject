"""Summarization, one module per source kind.

Both jobs are best-effort by design: a summary improves a list view and never
gates retrieval, so a provider failure here must not fail the document. That
constraint is enforced by the caller (`workers/enrich.py`), which logs and moves
on rather than retrying into the queue's dead-letter path.
"""

from __future__ import annotations

from app.ai.summarization.document_summary import build_prompt as build_document_summary_prompt
from app.ai.summarization.document_summary import clean as clean_summary
from app.ai.summarization.note_summary import build_prompt as build_note_summary_prompt

__all__ = [
    "build_document_summary_prompt",
    "build_note_summary_prompt",
    "clean_summary",
]
