"""Prompt construction.

The system prompt lives in app.ai.grounding (it is part of the contract that the
grounding tests exercise). This module only builds the user turn, keeping the
"what the model sees" logic in one readable place.
"""

from __future__ import annotations

from app.ai.grounding import Source, build_context

HISTORY_TURNS = 4


def build_user_turn(
    question: str,
    sources: list[Source],
    history: list[dict[str, str]] | None = None,
) -> str:
    parts: list[str] = []
    if history:
        recent = history[-HISTORY_TURNS:]
        transcript = "\n".join(
            f"{m['role']}: {m['content'][:400]}" for m in recent if m.get("content")
        )
        if transcript:
            parts.append(
                "Earlier in this conversation (for resolving references like "
                f"'that one'; do NOT cite from here):\n{transcript}"
            )

    parts.append(build_context(sources))
    parts.append(f"Question: {question.strip()}")
    parts.append(
        "Answer using only the sources above. Cite each factual sentence with [n]. "
        "If the sources do not contain the answer, say so plainly — do not guess."
    )
    return "\n\n".join(parts)


def build_entity_prompt(text: str, max_chars: int = 6000) -> str:
    return (
        "Extract entities and relations from the text below.\n"
        'Return strict JSON: {"entities":[{"name":str,"type":'
        '"person|organization|project|concept|place|event|other"}],'
        '"relations":[{"source":str,"target":str,"relation":str}]}\n'
        "Only include entities clearly named in the text. No commentary.\n\n"
        f"TEXT:\n{text[:max_chars]}"
    )


def build_summary_prompt(text: str, max_chars: int = 8000) -> str:
    return (
        "Summarize the document below in two or three sentences. State only what the text "
        "supports; do not generalize beyond it. No preamble.\n\n"
        f"DOCUMENT:\n{text[:max_chars]}"
    )
