"""Relations between entities, and the prompt that asks for them.

Kept separate from entity extraction because the two fail differently and are
worth reading separately: entity recognition is recall-oriented (miss a name and
the graph loses a node), relation extraction is precision-oriented (invent an
edge and the graph asserts something the text never said).

The rules encoded here reflect that asymmetry: a relation is only kept when both
endpoints were independently extracted, and self-loops are dropped because
"X relates to X" is noise the model produces freely.
"""

from __future__ import annotations

from app.ai.knowledge.entity_extractor import Extraction, parse

ENTITY_PROMPT = (
    "Extract entities and relations from the text below.\n"
    'Return strict JSON: {"entities":[{"name":str,"type":'
    '"person|organization|project|concept|place|event|other"}],'
    '"relations":[{"source":str,"target":str,"relation":str}]}\n'
    "Only include entities clearly named in the text. A relation may only use two "
    "entities you listed. No commentary.\n\n"
    "TEXT:\n{{text}}"
)

# How much of a long document the extractor reads. Entities in the tail of a
# 200-page PDF are missed; that is a known, bounded limitation rather than a
# pretend-everything promise, and it is why this job can be re-run per version.
MAX_CHARS = 6000


def build_prompt(text: str, max_chars: int = MAX_CHARS) -> str:
    """Substitute the text only.

    The prompt contains JSON braces, so `str.format` would treat them as fields.
    Replacing a single sentinel token is both correct and impossible to break by
    adding example JSON later.
    """
    return ENTITY_PROMPT.replace("{{text}}", text[:max_chars])


def extract(raw_model_output: str) -> Extraction:
    """Parse a model reply into entities and relations. Never raises."""
    return parse(raw_model_output)
