"""The streaming grounding guarantee, tested without a model.

## The bug these tests exist for

`stream_answer` used to do this:

    async for delta in llm.stream(...):
        yield ("delta", {"text": delta})          # sent to the browser
    answer = resolve_citations("".join(buffer), sources)   # verified afterwards

So a model answering "Your API key lives in Fly secrets [9]" streamed that
sentence to the user, and the `citations` event that followed reported
`grounded: false` with zero citations. The message stored in the database was
replaced by the refusal template — the database was right and the screen was
wrong. A model that emitted no citation at all was never retracted, because there
was no complete marker to notice.

These tests assert the property that closes it: **nothing reaches the user until a
real citation exists.**

## Why these are unit tests

The guarantee is a pure function over strings, so it can be asserted exactly,
byte for byte, with no model, no network and no database. That is the only kind of
test that can be trusted for a claim like "the user never sees ungrounded text" —
an end-to-end test with a real model proves it for one sample, once.
"""

from __future__ import annotations

import re

import pytest
from app.ai.rag.answer_generator import MARKER_RE
from app.ai.rag.stream_guard import GroundingStreamFilter

VALID = {1, 2, 3, 12, 123}


def stream_all(chunks: list[str], valid: set[int] | None = None) -> tuple[str, bool]:
    """Feed chunks the way `stream_answer` does; return (text shown, grounded)."""
    gate = GroundingStreamFilter(valid if valid is not None else VALID)
    shown: list[str] = []
    for chunk in chunks:
        safe = gate.feed(chunk)
        if safe:
            shown.append(safe)
    tail = gate.flush()
    if tail:
        shown.append(tail)
    return "".join(shown), gate.grounded


def _tokens(text: str) -> list[str]:
    """Split into word-ish chunks like a real tokenizer, boundries included.

    Each piece keeps its trailing space, because that is what a model streams —
    dropping the separators would concatenate the words into "Theanswerisin".
    """
    parts = re.split(r"(\s+)", text)
    return [p for p in parts if p]


class TestNothingIsReleasedWithoutACitation:
    def test_a_fabricated_marker_releases_nothing(self):
        shown, grounded = stream_all(_tokens("Your key lives in Fly secrets [9]."))
        assert shown == "", "text citing a non-existent source must never reach the user"
        assert grounded is False

    def test_an_answer_with_no_marker_at_all_releases_nothing(self):
        """The case a post-hoc marker check cannot catch: nothing to retract."""
        shown, grounded = stream_all(_tokens("Your key lives in Fly secrets."))
        assert shown == ""
        assert grounded is False

    def test_a_refusal_shaped_answer_without_sources_releases_nothing(self):
        shown, grounded = stream_all(["I don't know."])
        assert shown == ""
        assert grounded is False

    @pytest.mark.parametrize("fabricated", ["[9]", "[0]", "[42]", "[999]"])
    def test_only_markers_that_map_to_a_source_open_the_gate(self, fabricated):
        shown, grounded = stream_all(_tokens(f"The answer is {fabricated}"))
        assert shown == ""
        assert grounded is False


class TestGroundedAnswersStillStream:
    def test_a_real_marker_opens_the_gate(self):
        text, grounded = stream_all(_tokens("The answer is in the notes [1]."))
        assert grounded is True
        assert "The answer is in the notes" in text
        assert "[1]" in text

    def test_text_after_the_citation_streams_normally(self):
        chunks = _tokens("First claim [1]. Second claim follows here without its own citation.")
        text, grounded = stream_all(chunks)
        assert grounded is True
        assert "Second claim follows here" in text, "the rest of the answer must still stream"

    def test_a_citation_chunked_across_boundaries_is_released_intact(self):
        """A tokenizer may split `[1]` into `[`, `1`, `]`.

        Releasing `[1` would put a fragment on screen, and the client renders
        markers as chips — so a fragment is visible breakage, not a no-op.
        """
        shown: list[str] = []
        gate = GroundingStreamFilter(VALID)
        for piece in ["Answer is here ", "[", "1", "]", "."]:
            safe = gate.feed(piece)
            if safe:
                shown.append(safe)
        shown.append(gate.flush())
        text = "".join(shown)
        assert text == "Answer is here [1].", f"got {text!r}"
        assert gate.grounded is True

    def test_flush_releases_a_held_back_partial_marker(self):
        """The only thing held back at end-of-stream is an incomplete marker.

        Sentence text is never held back once the gate is open — it is released
        with the chunk that carried the citation, which is why the latency cost of
        this design is one chunk rather than one completion.
        """
        gate = GroundingStreamFilter(VALID)
        assert gate.feed("Claim [1] and then [") == "Claim [1] and then "
        # End of stream with a dangling "[" — it can never become a marker, so it
        # is released rather than swallowed.
        assert gate.flush() == "["


class TestFabricatedMarkersAreStrippedFromGroundedAnswers:
    def test_a_fabrication_after_a_real_citation_never_reaches_the_user(self):
        """The answer is genuinely grounded, but it also invents a source.

        Releasing [9] would make the screen disagree with the persisted message
        (which strips it), and would show the user a citation that resolves to
        nothing when clicked.
        """
        text, grounded = stream_all(_tokens("Real claim [1] and invented claim [9]."))
        assert grounded is True
        assert "[1]" in text
        assert "[9]" not in text, "a fabricated marker must be stripped even mid-stream"

    def test_stripping_does_not_damage_neighbouring_text(self):
        text, _ = stream_all(_tokens("A [1] B [7] C"))
        assert "[7]" not in text
        assert "A" in text and "B" in text and "C" in text


class TestMarkerRegexAgreesWithTheResolver:
    def test_the_two_patterns_are_identical(self):
        """`stream_guard` duplicates the regex to stay dependency-free.

        Duplication is only safe if something pins it. If the resolver started
        accepting `[12a]` and the gate did not, the gate would hold back an answer
        that the resolver considers grounded — a hang, not an error.
        """
        assert (
            MARKER_RE.pattern
            == __import__("app.ai.rag.stream_guard", fromlist=["MARKER_RE"]).MARKER_RE.pattern
        )

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("claim [1]", True),
            ("claim [12]", True),
            ("claim [123]", True),
            ("claim [1234]", False),  # more digits than the resolver accepts
            ("claim []", False),
            ("claim [a]", False),
            ("no marker at all", False),
        ],
    )
    def test_gate_opens_only_for_resolver_shaped_markers(self, text, expected):
        _, grounded = stream_all([text])
        assert grounded is expected


class TestGateIsSafeToFeedRepeatedly:
    def test_empty_deltas_are_ignored(self):
        gate = GroundingStreamFilter(VALID)
        assert gate.feed("") == ""
        assert gate.feed("Claim [1]") == "Claim [1]"
        assert gate.feed("") == ""
        assert gate.flush() == ""

    def test_flush_after_flush_returns_nothing(self):
        gate = GroundingStreamFilter(VALID)
        gate.feed("Claim [1]")
        assert gate.flush() == ""
        assert gate.flush() == ""

    def test_feeding_after_flush_releases_nothing(self):
        """A late delta (an aborted stream) must not resurrect the buffer."""
        gate = GroundingStreamFilter(VALID)
        gate.feed("Claim [1]")
        gate.flush()
        assert gate.feed("late text [1]") == ""

    def test_an_ungrounded_buffer_is_discarded_not_latently_held(self):
        """`flush()` on an ungrounded gate returns "" and forgets the text.

        The caller emits the refusal; if the buffer survived it would be one
        refactor away from being sent.
        """
        gate = GroundingStreamFilter(VALID)
        gate.feed("Ungrounded claim [9]")
        assert gate.flush() == ""
        assert gate.grounded is False
        assert gate._buffer == ""  # noqa: SLF001 - the whole point of the assertion
