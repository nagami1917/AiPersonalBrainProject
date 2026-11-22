"""Knowledge extraction: the model proposes, this code decides.

These tests are about rejection as much as acceptance. Entity recognition wants
recall; relation extraction wants precision, because an invented edge asserts
something the user's document never said — the same failure as a fabricated
citation, in graph form.
"""

from __future__ import annotations

from app.ai.knowledge import (
    build_entity_prompt,
    extract_knowledge,
    mentions,
    normalize_entity_name,
    parse,
)


class TestNormalization:
    def test_articles_and_case_collapse(self):
        assert normalize_entity_name("The Fly.io") == normalize_entity_name("fly.io")

    def test_whitespace_collapses(self):
        assert normalize_entity_name("  Postgres   Queue ") == "postgres queue"

    def test_it_is_capped(self):
        assert len(normalize_entity_name("x" * 500)) == 200


class TestParsing:
    def test_prose_wrapped_json_is_recovered(self):
        raw = (
            "Sure! Here you go:\n```json\n"
            '{"entities":[{"name":"Fly.io","type":"organization"}]}\n```'
        )
        extraction = parse(raw)
        assert [e.name for e in extraction.entities] == ["Fly.io"]

    def test_garbage_yields_nothing_instead_of_raising(self):
        assert parse("I cannot help with that.").is_empty

    def test_a_json_list_is_not_an_entity_payload(self):
        assert parse('["Fly.io", "Postgres"]').is_empty

    def test_unknown_type_becomes_other_not_a_rejection(self):
        """Losing a real entity because its label is unexpected is the worse error."""
        extraction = parse('{"entities":[{"name":"NEXUS","type":"spaceship"}]}')
        assert extraction.entities[0].entity_type == "other"

    def test_duplicate_entities_collapse_to_one(self):
        raw = '{"entities":[{"name":"Fly.io"},{"name":"the fly.io"}]}'
        assert len(parse(raw).entities) == 1

    def test_overlong_names_are_dropped(self):
        assert parse('{"entities":[{"name":"' + "x" * 300 + '"}]}').entities == []


class TestRelations:
    def test_a_relation_needs_both_endpoints_to_exist(self):
        raw = '{"entities":[{"name":"Fly.io"}],"relations":[{"source":"Fly.io","target":"Ghost"}]}'
        assert parse(raw).relations == []

    def test_a_valid_relation_survives(self):
        raw = (
            '{"entities":[{"name":"Fly.io"},{"name":"Postgres"}],'
            '"relations":[{"source":"Fly.io","target":"Postgres","relation":"runs"}]}'
        )
        relations = parse(raw).relations
        assert len(relations) == 1
        assert (relations[0].source, relations[0].target, relations[0].label) == (
            "fly.io",
            "postgres",
            "runs",
        )

    def test_self_loops_are_dropped(self):
        raw = (
            '{"entities":[{"name":"Fly.io"}],'
            '"relations":[{"source":"Fly.io","target":"Fly.io","relation":"is"}]}'
        )
        assert parse(raw).relations == []

    def test_a_missing_label_defaults_rather_than_failing(self):
        raw = (
            '{"entities":[{"name":"A"},{"name":"B"}],' '"relations":[{"source":"A","target":"B"}]}'
        )
        assert parse(raw).relations[0].label == "co_occurs"

    def test_relation_labels_are_length_capped(self):
        raw = (
            '{"entities":[{"name":"A"},{"name":"B"}],'
            '"relations":[{"source":"A","target":"B","relation":"' + "r" * 300 + '"}]}'
        )
        assert len(parse(raw).relations[0].label) == 80


class TestMentions:
    def test_it_matches_case_insensitively(self):
        assert mentions("Fly.io", "we deploy to FLY.IO nightly")

    def test_it_rejects_a_name_the_chunk_does_not_contain(self):
        """The rule that stops a graph edge the text never supports."""
        assert not mentions("Kubernetes", "we deploy to Fly.io nightly")

    def test_an_empty_name_never_matches(self):
        assert not mentions("", "any content at all")


class TestPrompt:
    def test_it_forbids_relations_between_unlisted_entities(self):
        assert "two entities you listed" in build_entity_prompt("text")

    def test_it_truncates_long_documents(self):
        prompt = build_entity_prompt("x" * 50_000, max_chars=100)
        assert len(prompt) < 500


class TestEndToEnd:
    def test_a_full_extraction_round_trips(self):
        raw = (
            '```json\n{"entities":[{"name":"Fly.io","type":"organization"},'
            '{"name":"Postgres","type":"concept"}],'
            '"relations":[{"source":"Fly.io","target":"Postgres","relation":"hosts"}]}\n```'
        )
        extraction = extract_knowledge(raw)
        assert {e.normalized for e in extraction.entities} == {"fly.io", "postgres"}
        assert extraction.relations[0].label == "hosts"
