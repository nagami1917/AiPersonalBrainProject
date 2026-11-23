"""Configuration aliases.

The deployment document names several settings differently from this codebase.
Both spellings are accepted, and these tests pin the three properties that make
that safe rather than merely convenient:

  1. a spec-only environment **boots** — a renamed secret is not an outage;
  2. the canonical name **wins** when both are set, so behaviour never depends
     on which one happens to be present;
  3. an alias cannot smuggle in an **inconsistent** pair — specifically a
     different embedding model with the old dimension, which is the one mismatch
     that produces an index that writes cleanly and retrieves nothing.

`Settings(_env_file=None)` is used throughout so a developer's local `.env`
cannot change the result of a test.
"""

from __future__ import annotations

import os

import pytest
from app.core.config import Settings
from pydantic import ValidationError

REQUIRED = {
    "SUPABASE_URL": "https://project.supabase.co",
    "SUPABASE_ANON_KEY": "anon",
    "SUPABASE_SERVICE_ROLE_KEY": "service",
    "SUPABASE_JWT_SECRET": "x" * 40,
}


def build(monkeypatch, **overrides) -> Settings:
    """Construct Settings from `overrides` and nothing else.

    Every relevant name is deleted first, so a value in the ambient environment
    or a checked-out `.env` cannot make a test pass for the wrong reason.
    """
    prefixes = (
        "SUPABASE",
        "OPENAI",
        "AI_",
        "EMBEDDING",
        "DATABASE",
        "ALLOWED",
        "MAX_UPLOAD",
        "CORS",
    )

    for key in list(os.environ):
        if key.startswith(prefixes):
            monkeypatch.delenv(key, raising=False)

    values = {**REQUIRED, **overrides}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


class TestDatabaseUrl:
    def test_database_url_is_used_directly(self, monkeypatch):
        settings = build(monkeypatch, DATABASE_URL="postgresql://a/b", OPENAI_API_KEY="k")
        assert settings.database_url == "postgresql://a/b"

    def test_supabase_db_url_is_accepted_as_the_spec_names_it(self, monkeypatch):
        settings = build(monkeypatch, SUPABASE_DB_URL="postgresql://spec/db", OPENAI_API_KEY="k")
        assert settings.database_url == "postgresql://spec/db"

    def test_database_url_wins_when_both_are_set(self, monkeypatch):
        settings = build(
            monkeypatch,
            DATABASE_URL="postgresql://canonical/db",
            SUPABASE_DB_URL="postgresql://spec/db",
            OPENAI_API_KEY="k",
        )
        assert settings.database_url == "postgresql://canonical/db"

    def test_neither_present_is_a_clear_error(self, monkeypatch):
        """Not a KeyError about a field name — a sentence that says what to set."""
        with pytest.raises(ValidationError) as exc:
            build(monkeypatch)
        assert "DATABASE_URL" in str(exc.value)


class TestAiAliases:
    def test_ai_api_key_maps_to_openai_api_key(self, monkeypatch):
        settings = build(monkeypatch, DATABASE_URL="postgresql://a/b", AI_API_KEY="sk-spec")
        assert settings.openai_api_key == "sk-spec"

    def test_openai_api_key_wins(self, monkeypatch):
        settings = build(
            monkeypatch,
            DATABASE_URL="postgresql://a/b",
            OPENAI_API_KEY="sk-canonical",
            AI_API_KEY="sk-spec",
        )
        assert settings.openai_api_key == "sk-canonical"

    def test_ai_model_maps_to_llm_model(self, monkeypatch):
        settings = build(
            monkeypatch, DATABASE_URL="postgresql://a/b", OPENAI_API_KEY="k", AI_MODEL="gpt-4o"
        )
        assert settings.llm_model == "gpt-4o"

    def test_a_missing_ai_key_fails_at_boot(self, monkeypatch):
        """A backend that boots without a key indexes nothing, silently."""
        with pytest.raises(ValidationError) as exc:
            build(monkeypatch, DATABASE_URL="postgresql://a/b")
        assert "OPENAI_API_KEY" in str(exc.value)


class TestEmbeddingPair:
    def test_the_default_pair_is_consistent(self, monkeypatch):
        settings = build(monkeypatch, DATABASE_URL="postgresql://a/b", OPENAI_API_KEY="k")
        assert (settings.pinned_embedding_model, settings.pinned_embedding_dim) == (
            "text-embedding-3-small",
            1536,
        )

    def test_switching_model_via_the_spec_name_requires_the_matching_dim(self, monkeypatch):
        """The failure this prevents is silent, so it must be loud.

        `EMBEDDING_MODEL=text-embedding-3-large` with the default dim=1536 would
        write 3072-value vectors into a 1536-dimension column, or index against a
        mismatched column — either way retrieval returns nothing and nothing
        errors.
        """
        with pytest.raises(ValidationError) as exc:
            build(
                monkeypatch,
                DATABASE_URL="postgresql://a/b",
                OPENAI_API_KEY="k",
                EMBEDDING_MODEL="text-embedding-3-large",
            )
        assert "3072" in str(exc.value)

    def test_the_consistent_pair_is_accepted(self, monkeypatch):
        settings = build(
            monkeypatch,
            DATABASE_URL="postgresql://a/b",
            OPENAI_API_KEY="k",
            EMBEDDING_MODEL="text-embedding-3-large",
            PINNED_EMBEDDING_DIM="3072",
        )
        assert settings.pinned_embedding_dim == 3072

    def test_an_explicit_mismatch_is_rejected_under_either_name(self, monkeypatch):
        with pytest.raises(ValidationError):
            build(
                monkeypatch,
                DATABASE_URL="postgresql://a/b",
                OPENAI_API_KEY="k",
                PINNED_EMBEDDING_MODEL="text-embedding-3-small",
                PINNED_EMBEDDING_DIM="3072",
            )


class TestCorsAliases:
    def test_allowed_origins_maps_to_cors(self, monkeypatch):
        settings = build(
            monkeypatch,
            DATABASE_URL="postgresql://a/b",
            OPENAI_API_KEY="k",
            ALLOWED_ORIGINS="https://app.example, https://preview.example",
        )
        assert settings.cors_origins == ["https://app.example", "https://preview.example"]

    def test_cors_allowed_origins_wins(self, monkeypatch):
        settings = build(
            monkeypatch,
            DATABASE_URL="postgresql://a/b",
            OPENAI_API_KEY="k",
            CORS_ALLOWED_ORIGINS="https://canonical.example",
            ALLOWED_ORIGINS="https://spec.example",
        )
        assert settings.cors_origins == ["https://canonical.example"]

    def test_a_wildcard_is_not_the_default(self, monkeypatch):
        """`*` with credentials is the classic CORS mistake; it must be opt-in."""
        settings = build(monkeypatch, DATABASE_URL="postgresql://a/b", OPENAI_API_KEY="k")
        assert "*" not in settings.cors_origins


class TestUploadSize:
    def test_the_spec_names_megabytes(self, monkeypatch):
        settings = build(
            monkeypatch,
            DATABASE_URL="postgresql://a/b",
            OPENAI_API_KEY="k",
            MAX_UPLOAD_SIZE_MB="25",
        )
        assert settings.max_upload_bytes == 25 * 1024 * 1024

    def test_bytes_win_when_both_are_set(self, monkeypatch):
        """The finer-grained unit is the canonical one; MB is an input form."""
        settings = build(
            monkeypatch,
            DATABASE_URL="postgresql://a/b",
            OPENAI_API_KEY="k",
            MAX_UPLOAD_SIZE_MB="25",
            MAX_UPLOAD_BYTES="1048576",
        )
        assert settings.max_upload_bytes == 1048576

    def test_the_default_is_a_real_limit(self, monkeypatch):
        settings = build(monkeypatch, DATABASE_URL="postgresql://a/b", OPENAI_API_KEY="k")
        assert settings.max_upload_bytes > 0
