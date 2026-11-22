"""Knowledge graph: entities, relations, and the writes that build the graph.

Split three ways because the failure modes differ:

  * `entity_extractor` — recall-oriented. Missing a name loses a node.
  * `relation_extractor` — precision-oriented. An invented edge asserts something
    the text never said, which is the worse error.
  * `graph_builder` — the database writes, with the rule that a mention link is
    only created for a chunk that literally contains the name.
"""

from __future__ import annotations

from app.ai.knowledge.entity_extractor import (
    Entity,
    Extraction,
    Relation,
    mentions,
    normalize_entity_name,
    parse,
)
from app.ai.knowledge.graph_builder import persist
from app.ai.knowledge.relation_extractor import build_prompt as build_entity_prompt
from app.ai.knowledge.relation_extractor import extract as extract_knowledge

__all__ = [
    "Entity",
    "Extraction",
    "Relation",
    "build_entity_prompt",
    "extract_knowledge",
    "mentions",
    "normalize_entity_name",
    "parse",
    "persist",
]
