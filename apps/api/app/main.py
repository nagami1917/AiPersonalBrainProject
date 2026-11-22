"""FastAPI application factory.

Startup order matters: configuration is validated (and fails fast) before the
pool is created, so a missing secret is a boot failure with a clear message
rather than a 500 on the first request.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    ai,
    aliases,
    auth,
    chat,
    collections,
    conversations,
    documents,
    graph,
    jobs,
    notes,
    search,
    stats,
    suggestions,
    workspaces,
)
from app.core.config import get_settings
from app.core.db import close_pool, get_pool
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, install_request_logging

log = logging.getLogger("nexus.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info("starting api (env=%s)", settings.environment)
    app.state.settings = settings
    await get_pool(settings)
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
    ):
        app.include_router(module.router, prefix="/v1")
    app.include_router(collections.tags_router, prefix="/v1")
    app.include_router(conversations.messages_router, prefix="/v1")

    # Flat, spec-shaped aliases. They resolve a workspace themselves (query
    # parameter, defaulting to the caller's own) and delegate to the scoped
    # handlers, so there is one implementation of each resource.
    app.include_router(aliases.flat, prefix="/v1")
    app.include_router(search.flat_router, prefix="/v1")
    app.include_router(graph.flat_router, prefix="/v1")

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": settings.api_version}

    @app.get("/v1/meta", tags=["meta"])
    async def meta() -> dict[str, object]:
        """Capability document: the frontend reads limits from here instead of
        hardcoding them (API.md §3.1)."""
        return {
            "version": settings.api_version,
            "supported_mime_types": list(settings.allowed_mime_types),
            "max_upload_bytes": settings.max_upload_bytes,
            "embedding_model": settings.pinned_embedding_model,
            "embedding_dim": settings.pinned_embedding_dim,
            "llm_model": settings.llm_model,
            "retrieval_k": settings.retrieval_k,
        }

    return app


app = create_app()
