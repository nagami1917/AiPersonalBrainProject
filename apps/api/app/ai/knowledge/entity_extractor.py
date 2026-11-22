"""Entities from text: ask the model, then distrust its answer.

The model returns free-form JSON, so everything here is validation:

  * strict JSON extraction, refusing anything that is not an object;
  * a fixed entity-type vocabulary (`other` is the fallback, never a rejection —
    losing a real entity because its type is unexpected is worse than a vague tag);
  * a length cap and normalisation, so "The Fly.io" and "fly.io" are one entity;
  * every candidate is only linked to chunks that literally contain its name.
    That last rule is the important one: a mention link the text does not support
    is a fabricated citation wearing a different hat.

No database access, no prompt text — those live in `prompts.py` and
`graph_builder.py` respectively, so this module is testable against fixed strings.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

VALID_TYPES = {"person", "organization", "project", "concept", "place", "event", "other"}

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


@dataclass(frozen=True)
class Entity:
    name: str
    normalized: str
    entity_type: str


@dataclass(frozen=True)
class Relation:
    source: str  # normalized
    target: str  # normalized
    label: str


@dataclass
class Extraction:
    entities: list[Entity] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.entities and not self.relations


def normalize_entity_name(name: str) -> str:
    """Case- and article-insensitive key, so one entity is one row."""
    cleaned = re.sub(r"^(the|a|an)\s+", "", name.strip().lower())
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:200]


def parse_json_object(raw: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model reply, or give up quietly.

    Models wrap JSON in prose and code fences. Anything that is not a dict is
    rejected rather than coerced: a list of strings is not an entity payload.
    """
    match = _JSON_BLOCK.search(raw)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def parse(raw: str) -> Extraction:
    """Model output → validated entities and relations. Never raises."""
    data = parse_json_object(raw)
    if data is None:
        return Extraction()

    entities: list[Entity] = []
    seen: set[str] = set()
    for item in data.get("entities", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name or len(name) > 200:
            continue
        normalized = normalize_entity_name(name)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        entity_type = str(item.get("type", "other")).lower()
        if entity_type not in VALID_TYPES:
            entity_type = "other"
        entities.append(Entity(name=name, normalized=normalized, entity_type=entity_type))

    known = {e.normalized for e in entities}
    relations: list[Relation] = []
    for item in data.get("relations", []):
        if not isinstance(item, dict):
            continue
        source = normalize_entity_name(str(item.get("source", "")))
        target = normalize_entity_name(str(item.get("target", "")))
        # Both ends must be entities we actually extracted, or the edge would
        # point at nothing.
        if not source or not target or source == target:
            continue
        if source not in known or target not in known:
            continue
        label = str(item.get("relation", "co_occurs"))[:80].strip() or "co_occurs"
        relations.append(Relation(source=source, target=target, label=label))

    return Extraction(entities=entities, relations=relations)


def mentions(name: str, content: str) -> bool:
    """Does this chunk actually contain the entity name?

    Substring, case-insensitive, on the *original* name — deliberately simple.
    Fuzzy matching here would link entities the user never wrote, and a wrong
    graph edge is worse than a missing one.
    """
    return bool(name) and name.lower() in content.lower()
