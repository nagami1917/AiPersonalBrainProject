"""Summaries for a whole document.

The constraint that shapes this: a summary is shown next to the document and used
to help a user decide whether to open it. It is **not** a source. Nothing here
feeds retrieval, so a mediocre summary degrades a list view rather than an answer.

`text[:MAX_CHARS]` is a real limitation, not an oversight: a 300-page PDF is
summarised from its opening section. Making that honest matters more than making
it sound comprehensive — the prompt tells the model to state only what the text
supports, and the stored summary is capped by `MAX_SUMMARY_CHARS`.
"""

from __future__ import annotations

MAX_CHARS = 8000
MAX_SUMMARY_CHARS = 2000

SUMMARY_PROMPT = (
    "Summarize the document below in two or three sentences. State only what the text "
    "supports; do not generalize beyond it. No preamble.\n\n"
    "DOCUMENT:\n{text}"
)


def build_prompt(text: str, max_chars: int = MAX_CHARS) -> str:
    return SUMMARY_PROMPT.format(text=text[:max_chars])


def clean(summary: str) -> str:
    """Trim to the column's practical ceiling, without cutting mid-word ugly."""
    text = summary.strip()
    if len(text) <= MAX_SUMMARY_CHARS:
        return text
    cut = text[:MAX_SUMMARY_CHARS]
    boundary = max(cut.rfind(". "), cut.rfind("\n"))
    if boundary > MAX_SUMMARY_CHARS // 2:
        cut = cut[: boundary + 1]
    return cut.strip()
