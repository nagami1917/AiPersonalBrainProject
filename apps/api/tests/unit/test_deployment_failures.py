"""Deployment-only failure modes, pinned as tests.

Each case here is something that passes every local check and then fails in
production, so the assertions are written against the *deployment* shape rather
than the development one:

  1. **Supavisor and prepared statements.** `DEPLOYMENT.md` requires the pooled
     connection string (:6543). asyncpg caches prepared statements per
     connection, and the pooler multiplexes clients onto different backends, so
     a cached statement can be executed on a server that never prepared it. The
     pool must therefore be created with `statement_cache_size=0`. Verified by
     reading the arguments the pool is actually constructed with, not by
     "it worked locally", because a direct connection never reproduces it.

  2. **The development CORS default in production.** `http://localhost:5173`
     is correct locally and silently wrong in production: Supabase auth still
     succeeds (the SDK calls the Supabase origin), so the failure appears only
     when the frontend first calls the API. Refused at boot instead.

  3. **`SENTRY_DSN` that does nothing.** The setting existed and was ignored,
     so an operator who configured it believed errors were being reported. The
     test asserts the DSN actually reaches the SDK, and — just as important —
     that an absent DSN or a missing SDK does not stop the app booting.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.core import db
from app.core.config import Settings
from app.core.error_tracking import init_error_tracking
from pydantic import ValidationError

REQUIRED = {
    "SUPABASE_URL": "https://project.supabase.co",
    "SUPABASE_ANON_KEY": "anon",
    "SUPABASE_SERVICE_ROLE_KEY": "service",
    "SUPABASE_JWT_SECRET": "x" * 40,
    "DATABASE_URL": "postgresql://user:pw@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres",
    "OPENAI_API_KEY": "sk-test",
}

_CLEARED_PREFIXES = ("SUPABASE", "OPENAI", "AI_", "EMBEDDING", "DATABASE", "CORS", "ALLOWED")


def build(monkeypatch, **overrides) -> Settings:
    """Settings from `overrides` only — ambient env and `.env` must not leak in."""
    for key in list(os.environ):
        if key.startswith(_CLEARED_PREFIXES) or key in ("ENVIRONMENT", "SENTRY_DSN"):
            monkeypatch.delenv(key, raising=False)
    for key, value in {**REQUIRED, **overrides}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


class TestPoolSurvivesThePooler:
    @pytest.mark.asyncio
    async def test_pool_disables_the_prepared_statement_cache(self, monkeypatch):
        """The pooler runs in transaction mode, where cached statements are unsafe."""
        settings = build(monkeypatch)
        fake_pool = MagicMock()

        with patch.object(db.asyncpg, "create_pool", AsyncMock(return_value=fake_pool)) as create:
            await db.get_pool(settings)
            db._pool = None  # do not leak the fake into another test

        kwargs = create.await_args.kwargs
        assert kwargs["statement_cache_size"] == 0, (
            "asyncpg must not cache prepared statements when talking to Supavisor; "
            "a cached statement is prepared on one backend and executed on another"
        )

    @pytest.mark.asyncio
    async def test_statement_timeout_is_still_set_as_a_server_setting(self, monkeypatch):
        """The pooler fix must not have dropped the timeout that guards a wedged query."""
        settings = build(monkeypatch)

        with patch.object(db.asyncpg, "create_pool", AsyncMock(return_value=MagicMock())) as create:
            await db.get_pool(settings)
            db._pool = None

        assert "statement_timeout" in create.await_args.kwargs["server_settings"]


class TestCorsIsNotTheDevelopmentDefaultInProduction:
    def test_production_refuses_the_localhost_default(self, monkeypatch):
        with pytest.raises(ValidationError) as exc:
            build(monkeypatch, ENVIRONMENT="production")
        message = str(exc.value)
        assert "CORS_ALLOWED_ORIGINS" in message
        assert "localhost" in message

    def test_production_accepts_a_real_origin(self, monkeypatch):
        settings = build(
            monkeypatch,
            ENVIRONMENT="production",
            CORS_ALLOWED_ORIGINS="https://nexus-final-thesis.vercel.app",
        )
        assert "nexus-final-thesis" in settings.cors_allowed_origins

    def test_local_keeps_the_default(self, monkeypatch):
        """The guard must not make local development worse."""
        settings = build(monkeypatch)
        assert settings.cors_allowed_origins == "http://localhost:5173"

    def test_preview_is_allowed_to_use_the_default(self, monkeypatch):
        """`preview` is often a single box with no frontend deployed yet."""
        settings = build(monkeypatch, ENVIRONMENT="preview")
        assert settings.cors_allowed_origins == "http://localhost:5173"


class TestErrorTrackingIsActuallyWired:
    def test_no_dsn_means_no_sdk_call_and_no_failure(self, monkeypatch):
        settings = build(monkeypatch)
        with patch("sentry_sdk.init") as sdk_init:
            assert init_error_tracking(settings) is False
            sdk_init.assert_not_called()

    def test_a_configured_dsn_reaches_the_sdk(self, monkeypatch):
        settings = build(monkeypatch, SENTRY_DSN="https://key@sentry.example.com/1")
        with patch("sentry_sdk.init") as sdk_init:
            assert init_error_tracking(settings) is True
        kwargs = sdk_init.call_args.kwargs
        assert kwargs["dsn"] == "https://key@sentry.example.com/1"
        assert kwargs["environment"] == "local"

    def test_probe_requests_are_not_reported(self, monkeypatch):
        """Fly hits /health and /ready constantly; reporting them would flood the quota."""
        settings = build(monkeypatch, SENTRY_DSN="https://key@sentry.example.com/1")
        with patch("sentry_sdk.init") as sdk_init:
            init_error_tracking(settings)
        before_send = sdk_init.call_args.kwargs["before_send"]

        assert before_send({"request": {"url": "https://api.example.com/ready"}}, {}) is None
        assert before_send({"request": {"url": "https://api.example.com/health"}}, {}) is None
        real_error = {"request": {"url": "https://api.example.com/api/v1/chat"}}
        assert before_send(real_error, {}) is real_error

    def test_a_broken_dsn_does_not_stop_startup(self, monkeypatch):
        settings = build(monkeypatch, SENTRY_DSN="https://key@sentry.example.com/1")
        with patch("sentry_sdk.init", side_effect=RuntimeError("vendor is down")):
            assert init_error_tracking(settings) is False

    def test_a_missing_sdk_is_a_warning_not_a_crash(self, monkeypatch):
        """An image built without the optional dependency must still boot."""
        settings = build(monkeypatch, SENTRY_DSN="https://key@sentry.example.com/1")
        # `from sentry_sdk import x` on a None module raises ImportError, which is
        # what a `pip install` without the optional extra produces.
        with patch.dict("sys.modules", {"sentry_sdk": None}):
            assert init_error_tracking(settings) is False
