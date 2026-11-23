"""FastAPI application factory.

Startup order matters: configuration is validated (and fails fast) before the
pool is created, so a missing secret is a boot failure with a clear message
rather than a 500 on the first request.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    ai,
    aliases,
    auth,
    chat,
    collections,
    conversations,
    documents,
    embeddings,
    graph,
    jobs,
    notes,
    search,
    stats,
    suggestions,
    workspaces,
)
from app.core.config import API_PREFIX, get_settings
from app.core.db import close_pool, get_pool
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, install_request_logging
from app.core.schema_guard import verify_schema

log = logging.getLogger("nexus.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info("starting api (env=%s)", settings.environment)
    app.state.settings = settings
    pool = await get_pool(settings)
    # Boot-time schema guards, after the pool exists and before the first
    # request. A width mismatch here is a deploy that would have failed later,
    # one chunk at a time, with a misleading retry loop.
    await verify_schema(pool, settings)

    # Drift warning. `verify_schema` catches a model whose *width* cannot fit
    # the column and refuses to boot; this catches the case that boots fine and
    # is worse — a model swap at the same width, which leaves old vectors being
    # ranked against new query vectors. Nothing can fix that at startup (it is
    # thousands of embedding calls), so it is reported loudly and the operator
    # runs POST …/embeddings/reindex.
    #
    # Wrapped in a broad except: a failed count must never prevent the API from
    # serving. It is a warning, not a guard.
    try:
        from app.services.reindex_service import needs_reindex_anywhere

        async with pool.acquire() as conn:
            stale = await needs_reindex_anywhere(conn, settings)
        if stale:
            log.warning(
                "embedding model drift: %d chunk(s) were indexed with a model other than %s. "
                "Retrieval quality is degraded until they are reprocessed — "
                "POST /api/v1/workspaces/{id}/embeddings/reindex with {confirm:true}.",
                stale,
                settings.pinned_embedding_model,
            )
    except Exception as exc:  # noqa: BLE001 - never block startup on a warning
        log.debug("embedding drift check skipped: %s", exc)

    try:
        yield
    finally:
        await close_pool()
        log.info("api stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="NEXUS API",
        version=settings.api_version,
        description=(
            "Personal knowledge management. Every answer is retrieved from the caller's "
            "own documents and cited; nothing is answered from model memory."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
    )

    install_request_logging(app)
    register_error_handlers(app)

    for module in (
        workspaces,
        documents,
        notes,
        collections,
        search,
        stats,
        suggestions,
        chat,
        conversations,
        graph,
        jobs,
        auth,
        ai,
        embeddings,
    ):
        app.include_router(module.router, prefix=API_PREFIX)
    app.include_router(collections.tags_router, prefix=API_PREFIX)
    app.include_router(conversations.messages_router, prefix=API_PREFIX)

    # Flat, spec-shaped aliases. They resolve a workspace themselves (query
    # parameter, defaulting to the caller's own) and delegate to the scoped
    # handlers, so there is one implementation of each resource.
    app.include_router(aliases.flat, prefix=API_PREFIX)
    app.include_router(search.flat_router, prefix=API_PREFIX)
    app.include_router(graph.flat_router, prefix=API_PREFIX)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Liveness only: is this process running and able to answer?

        Deliberately does not touch the database. This is the endpoint Fly's
        health check polls every 30s, and a liveness probe that depends on a
        downstream service will restart a perfectly healthy process when that
        service blips — turning a 30-second database hiccup into a rolling
        restart. Use `/ready` to ask whether the API can actually serve.
        """
        return {"status": "ok", "version": settings.api_version}

    @app.get("/ready", tags=["meta"])
    async def ready() -> JSONResponse:
        """Readiness: can this instance serve a request *right now*?

        Verified against a live pool rather than assumed: the old `/health`
        returned `ok` with a DSN pointing at a closed port, so a deploy whose
        database credentials were wrong would report healthy while every real
        request failed. A probe that cannot fail is not a probe.

        Returns 503 with a machine-readable reason on failure, so an operator
        (or `curl -f` in DEPLOYMENT.md) sees why rather than a bare 500.
        """
        try:
            pool = await get_pool(settings)
            # A cheap round-trip that proves a connection can be acquired and a
            # query can run — not a cached object in process memory.
            await asyncio.wait_for(pool.fetchval("select 1"), timeout=2.0)
        except Exception as exc:  # noqa: BLE001 — any failure is "not ready"
            # The message is a class name, never the exception text: a DSN parse
            # error can quote the connection string, which contains a password.
            log.warning("readiness check failed (%s)", type(exc).__name__)
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unavailable",
                    "version": settings.api_version,
                    "database": "unreachable",
                },
            )
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "version": settings.api_version, "database": "ok"},
        )

    @app.get(f"{API_PREFIX}/meta", tags=["meta"])
    async def meta() -> dict[str, object]:
        """Capability document: the frontend reads limits from here instead of
        hardcoding them (API.md §3.1).

        Rate limits are published because a 429 with no stated budget is a
        mystery to the client: it cannot back off sensibly or explain itself. The
        value is the honest one — a single-process counter — and
        `rate_limit_distributed: false` says so rather than implying a precision
        the implementation does not have.
        """
        return {
            "version": settings.api_version,
            "supported_mime_types": list(settings.allowed_mime_types),
            "max_upload_bytes": settings.max_upload_bytes,
            "embedding_model": settings.pinned_embedding_model,
            "embedding_dim": settings.pinned_embedding_dim,
            "llm_model": settings.llm_model,
            "retrieval_k": settings.retrieval_k,
            "rate_limits": {
                "enabled": settings.rate_limit_enabled,
                "window_seconds": settings.rate_limit_window_seconds,
                "per_user_ai_calls": settings.rate_limit_user_calls,
                "per_workspace_ai_calls": settings.rate_limit_workspace_calls,
                "distributed": False,
            },
        }

    return app


app = create_app()
