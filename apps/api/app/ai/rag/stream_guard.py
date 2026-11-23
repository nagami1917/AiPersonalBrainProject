"""Holding back model output until it is *provably* grounded — then streaming it.

## The problem this exists to solve

Streaming and the grounding contract are in direct tension, and the original
implementation resolved the tension by quietly abandoning the contract:

    async for delta in llm.stream(...):
        buffer.append(delta)
        yield ("delta", {"text": delta})     # <-- emitted BEFORE verification
    answer = resolve_citations("".join(buffer), sources)

The verification happened, but by then the user had already read the text. A
model that answered "Your API key lives in Fly secrets [9]" — marker 9 does not
exist — streamed that sentence to the browser, and only afterwards did the
`citations` event report `grounded: false` with zero citations. The persisted
message was substituted with the refusal template, so the database was correct
and the *screen* was wrong. Worse: a model that answered with no citation at all
was never retracted, because there was no complete marker to notice.

That is the single failure this product exists to prevent, arriving through the
one path most users take (the UI uses `/chat/stream`).

## The approach: holdback until the first valid citation

The rule is simple and checkable:

  **No text reaches the user until the model has cited a real source.**

Concretely, the filter withholds everything until the accumulating output
contains a marker that maps to an actual retrieved chunk. From that point on the
answer is demonstrably grounded in *something*, so the rest streams normally.
This is not "buffer the whole answer":

  * a grounded answer typically cites in its first sentence or two, so the
    perceived latency cost is a handful of tokens, not a whole completion;
  * an ungrounded answer emits nothing at all, and the caller sends the refusal —
    which is the correct outcome, not a compromise;
  * fabricated markers *after* the first valid one are stripped from the stream,
    so what the user reads matches what gets persisted.

The alternative — buffer everything, resolve, then emit — costs a full generation
of latency on every turn. The alternative of streaming unverified text and
retracting it is not a solution: a retraction the user has already read has
already done the damage.

## Why this is a separate, network-free module

The guarantee has to be testable without a model, or it is a hope. Everything
here is a pure function over strings, so the tests assert the exact bytes the
user would have seen.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# A complete citation marker. Matches `answer_generator.MARKER_RE` by
# construction; duplicated rather than imported to keep this module dependency
# free, and asserted equal in the tests so the two cannot drift silently.
MARKER_RE = re.compile(r"\[(\d{1,3})\]")

# A marker that might be completed by the next chunk: "[", "[1", "[12".
# Only at the very end of the buffer — anywhere else it is just prose.
_PARTIAL_MARKER_RE = re.compile(r"\[(\d{0,3})$")


class GroundingStreamFilter:
    """Incremental gate: emits model text only once a real citation exists.

    Usage is feed-as-you-go, then `flush()`:

        gate = GroundingStreamFilter(valid_markers={1, 2, 3})
        async for delta in llm.stream(...):
            safe = gate.feed(delta)
            if safe:
                yield safe
        tail = gate.flush()
        if tail:
            yield tail
        if not gate.grounded:
            ...emit refusal instead of the (never-sent) text...

    `flush()` returns "" when the gate never opened, so the caller is forced to
    take the ungrounded branch rather than accidentally emitting the buffer.
    """

    __slots__ = ("_valid", "_buffer", "_grounded", "_closed")

    def __init__(self, valid_markers: Iterable[int]) -> None:
        self._valid: frozenset[int] = frozenset(valid_markers)
        # Text accepted from the model but not yet released to the caller.
        self._buffer = ""
        self._grounded = False
        self._closed = False

    @property
    def grounded(self) -> bool:
        """True once a real citation has appeared (i.e. the gate has opened)."""
        return self._grounded

    def _strip_fabricated(self, text: str) -> str:
        """Remove markers that name no real source.

        Applied to text that is about to be released. A fabricated marker must
        not reach the screen even in an otherwise-grounded answer — the persisted
        message strips them, so releasing them would make the transcript on
        screen disagree with the transcript in the database.
        """

        def replace(match: re.Match[str]) -> str:
            return match.group(0) if int(match.group(1)) in self._valid else ""

        return MARKER_RE.sub(replace, text)

    def _has_valid_marker(self, text: str) -> bool:
        return any(int(m.group(1)) in self._valid for m in MARKER_RE.finditer(text))

    def feed(self, delta: str) -> str:
        """Accept a chunk; return the text that is safe to send right now.

        Before the gate opens this is always "" — the answer exists but is not
        yet evidence-backed, so nothing is released.
        """
        if self._closed or not delta:
            return ""

        self._buffer += delta

        if not self._grounded:
            if not self._has_valid_marker(self._buffer):
                return ""
            self._grounded = True

        return self._release(final=False)

    def flush(self) -> str:
        """Release whatever is safe at end-of-stream; "" if never grounded.

        Returning "" for an ungrounded answer is what makes the replacement
        possible: the caller has emitted nothing, so there is nothing to retract.
        """
        if self._closed:
            return ""
        self._closed = True
        if not self._grounded:
            # Deliberately discards the buffer. The caller sends the refusal.
            self._buffer = ""
            return ""
        return self._release(final=True)

    def _release(self, *, final: bool) -> str:
        """Emit the buffer, holding back a trailing partial marker.

        Holding back matters at chunk boundaries: the model may stream "[", then
        "1", then "]". Releasing "[1" would put a fragment on screen; the client
        renders markers as chips, so a fragment is visible breakage.
        """
        text = self._buffer
        if not final:
            partial = _PARTIAL_MARKER_RE.search(text)
            if partial and not MARKER_RE.search(text, partial.start()):
                # Keep the incomplete marker for the next call.
                text, self._buffer = text[: partial.start()], text[partial.start() :]
            else:
                self._buffer = ""
        else:
            self._buffer = ""

        return self._strip_fabricated(text)
