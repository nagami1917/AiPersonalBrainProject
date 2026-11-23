"""Conversation titles: derived from the first question, deterministically.

## Why not the LLM

A title is a summarisation task, and the obvious implementation is "ask the model".
That costs a second call on every new conversation — including the ones the user
abandons — and it competes with the answer for the same rate limit. The first
question is already a good title in the overwhelming majority of cases: it is
what the user thought was worth asking, in their own words.

So this is a pure function over the question. Consequences that matter:

  * zero latency and zero cost on conversation creation;
  * the same question always produces the same title, so it is testable;
  * a bad title is cheap to fix (`PATCH /conversations/{id}` already exists), and
    the user can see it immediately because it is their own words.

## Why truncating on a word boundary matters

A naive `question[:60]` cuts mid-word ("What did I decide about retriev"), which
looks like a rendering bug. Cutting at the last space and appending an ellipsis
is the difference between a truncated title and a broken-looking one.

## Why greetings and one-word questions still get a title

A conversation titled `` (empty) or "?" is unfindable in a list. The fallback is
"New conversation", which the sidebar can group; that is honest — it says the
system had nothing to work with rather than inventing a topic.
"""

from __future__ import annotations

# Long enough for the gist of a real question, short enough to fit a sidebar row
# without wrapping. Titles are display strings, not keys — nothing joins on them.
TITLE_MAX_CHARS = 60

FALLBACK_TITLE = "New conversation"

# Questions that carry no topic. Matched against the whitespace-stripped,
# lowercased question, so "  Hi!  " and "hi!" both hit. Deliberately a small
# list: this is not an attempt to classify intent, only to avoid titling a
# conversation "hi".
_NON_TOPICS = frozenset(
    {
        "hi",
        "hey",
        "hello",
        "yo",
        "thanks",
        "thank you",
        "ok",
        "okay",
        "test",
        "help",
        "what can you do",
        "what can you do?",
    }
)


def derive_title(question: str) -> str:
    """A display title for a conversation, from its opening question.

    Falls back to `FALLBACK_TITLE` when the question has no usable topic. The
    caller does not need to distinguish those cases: a titled conversation and a
    fallback-titled one are both listable, and the user can rename either.
    """
    collapsed = " ".join(question.split())
    if not collapsed:
        return FALLBACK_TITLE

    # Strip a trailing question mark before the topic check so "help?" matches,
    # but keep it in the title itself: it is part of how the user wrote it.
    probe = collapsed.rstrip("?!. ").lower()
    if not probe or probe in _NON_TOPICS:
        return FALLBACK_TITLE

    if len(collapsed) <= TITLE_MAX_CHARS:
        return collapsed

    cut = collapsed[:TITLE_MAX_CHARS]
    space = cut.rfind(" ")
    # No space in the first N chars means a single long token (a URL, a hash, a
    # German compound). Cutting it is still better than an overflowing row, but
    # only cut at the space when there is one past the halfway point — otherwise
    # a short first word would truncate the title to almost nothing.
    if space > TITLE_MAX_CHARS // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:") + "…"
