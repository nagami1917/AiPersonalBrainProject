"""Summaries for notes.

A note has no separate storage column: its body lives in `notes.content` (the
note text) and the summary goes to the same `summary` column a document uses. So
the only real difference from `document_summary` is size — notes are short enough
that the whole body is sent, and the "did we truncate?" warning that matters for
PDFs does not apply.

Kept as a separate module rather than a `kind` flag because the two have
different inputs (a note's body is already clean text; a document's `raw_text`
came out of a PDF extractor) and will diverge further once notes get
frontmatter-aware handling.
"""

from __future__ import annotations

from app.ai.summarization.document_summary import clean

MAX_CHARS = 8000

NOTE_PROMPT = (
    "Summarize this note in one or two sentences, in the user's own framing. "
    "State only what the note says; do not add context it does not contain.\n\n"
    "NOTE:\n{text}"
)


def build_prompt(text: str, max_chars: int = MAX_CHARS) -> str:
    return NOTE_PROMPT.format(text=text[:max_chars])


def normalize(summary: str) -> str:
    return clean(summary)
