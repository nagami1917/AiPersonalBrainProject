"""Conversation titles: derived at creation, deterministic, and testable.

## The gap these tests cover

`conversations.title` was nullable and nothing ever wrote it. Every conversation
in the database had `title = NULL`, so the conversation list — the thing that makes
a chat history navigable — showed a column of blanks. The schema had the field and
the API could PATCH it; the product simply never filled it in.

## Why the title is not model-generated

See `ai/conversation_title.py`: an LLM call per new conversation costs a model
round trip on every abandoned thread and competes with the answer for the same
rate limit. The first question is already the user's own summary of what they
wanted, so it is used verbatim, truncated on a word boundary.

That makes the whole feature a pure function, which is why it can be tested here
exhaustively instead of with one happy-path example.
"""

from __future__ import annotations

import pytest
from app.ai.conversation_title import FALLBACK_TITLE, TITLE_MAX_CHARS, derive_title


class TestTitlesFromRealQuestions:
    @pytest.mark.parametrize(
        "question",
        [
            "Where is the Supabase service role key stored?",
            "What did I decide about retrieval scoring?",
            "Summarize what I know about the job queue.",
            "How does the worker claim jobs?",
        ],
    )
    def test_a_question_becomes_its_own_title(self, question):
        """The common case: short enough to keep verbatim, question mark included."""
        assert derive_title(question) == question

    def test_whitespace_is_collapsed(self):
        """A pasted multi-line question must not become a multi-line title."""
        assert derive_title("  where   is\nthe key  ") == "where is the key"

    def test_a_long_question_is_truncated_on_a_word_boundary(self):
        question = (
            "What was the final decision about which retrieval scoring function to use "
            "for the hybrid search implementation?"
        )
        title = derive_title(question)
        assert len(title) <= TITLE_MAX_CHARS + 1  # +1 for the ellipsis
        assert title.endswith("…")
        # The character before the ellipsis must not be a partial word: the title
        # must be a prefix of the question up to a space.
        assert question.startswith(title[:-1].rstrip())


class TestFallbacks:
    @pytest.mark.parametrize("greeting", ["hi", "Hi!", "  hello  ", "thanks", "ok", "test", "help"])
    def test_a_greeting_does_not_become_a_title(self, greeting):
        """A sidebar full of "hi" is as useless as a sidebar of blanks."""
        assert derive_title(greeting) == FALLBACK_TITLE

    def test_an_empty_question_falls_back(self):
        assert derive_title("") == FALLBACK_TITLE
        assert derive_title("   ") == FALLBACK_TITLE

    def test_a_long_unbroken_token_is_still_cut(self):
        """A pasted URL or hash has no space to break on."""
        title = derive_title("a" * 200)
        assert len(title) <= TITLE_MAX_CHARS + 1
        assert title.endswith("…")

    def test_a_short_first_word_does_not_truncate_the_title_to_nothing(self):
        """The ≥half-length guard, which a naive `rfind(" ")` would break.

        "An extraordinarilylongsecondword…" has its only space at index 2; cutting
        there would produce "An…". The guard keeps the harder cut instead.
        """
        title = derive_title("An " + "b" * 120)
        assert len(title) > TITLE_MAX_CHARS // 2


class TestDeterminism:
    def test_the_same_question_always_yields_the_same_title(self):
        q = "What did I decide about retrieval scoring?"
        assert {derive_title(q) for _ in range(5)} == {q}

    def test_the_title_is_non_empty_for_every_non_empty_question(self):
        """The one invariant the sidebar depends on: a row always has a label."""
        for question in ["x", "?", "!!!", "  \n ", "a?", "日本", "🙂"]:
            assert derive_title(question), f"{question!r} produced an empty title"
