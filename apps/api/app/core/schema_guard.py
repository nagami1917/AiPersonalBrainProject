"""Startup guards for assumptions the database has to agree with.

## Why this module exists

`config.py` validates that the configured embedding **model** matches its
declared **dimension**. That check is necessary and was not sufficient: it
compares settings against a table of known models, not against the actual
`document_chunks.embedding` column.

The column is `vector(1536)` — pinned at migration time, because the HNSW index
is built for one width and cannot be rebuilt per request. A deployment that set
`EMBEDDING_MODEL=text-embedding-3-large` (3072 dims) passed every setting check,
booted cleanly, and then failed on the *first chunk insert* with a Postgres
dimension error. Ingestion appeared to accept the file and then never became
searchable.

Migration `0003` already ships `public.assert_embedding_dim()` for exactly this,
and `0011_grants.sql` grants it. Nothing called it. Calling it at startup turns
a silent, async ingest failure into a boot failure that names both values —
the same principle as `config.py`'s first rule, applied one layer down.

## Why startup and not ingest time

Ingest already fails loudly *per word* on a bad vector, and the worker retries,
which turns a permanent schema mismatch into an endless retry loop with a
misleading "temporary" label. Checking once at boot costs two catalog lookups
and cannot be forgotton by a future caller. The ingest path is not modified:
if the process started, the width is correct.
"""

from __future__ import annotations

import logging

import asyncpg

from app.core.config import Settings

log = logging.getLogger("nexus.schema")


class EmbeddingDimensionMismatch(RuntimeError):
    """Configured embedding dimension disagrees with the database column.

    Deliberately a `RuntimeError` and not a `NexusError`: this never maps to an
    HTTP response, because the process will not reach the point of serving one.
    """


async def assert_embedding_dimension(conn: asyncpg.Connection, settings: Settings) -> None:
    """Fail if code and schema disagree about the vector width.

    `embedding_dim()` is the authority, because it is defined next to the column
    and changes with it in a migration. The Python-side comparison is what makes
    the failure readable — it names the model, the configured width and the
    column's width in one line, where the SQL error is a bare exception string.

    Callers must ensure the schema exists first: `verify_schema()` checks for
    `embedding_dim()` and skips with a warning when migrations have not been
    applied, so this function may assume its queries will resolve.
    """
    db_dim = await conn.fetchval("select public.embedding_dim()")
    configured = settings.pinned_embedding_dim

    if db_dim != configured:
        raise EmbeddingDimensionMismatch(
            "Embedding dimension mismatch: "
            f"PINNED_EMBEDDING_MODEL={settings.pinned_embedding_model} "
            f"produces {configured}-dim vectors, but public.document_chunks.embedding is "
            f"vector({db_dim}). Ingestion would fail on the first chunk. Either set a model "
            f"that produces {db_dim} dims, or add a migration that rescales the column and "
            f"rebuilds idx_chunks_embedding_hnsw (docs/DATABASE.md)."
        )

    # The SQL guard also checks the column against the function; calling it makes
    # the assertion true even if the two disagree in a way the query above misses.
    await conn.execute("select public.assert_embedding_dim()")
    log.info(
        "embedding dimension verified: %s / %s dims",
        settings.pinned_embedding_model,
        configured,
    )


async def _function_exists(conn: asyncpg.Connection, name: str) -> bool:
    return bool(
        await conn.fetchval(
            """
            select exists(
              select 1 from pg_proc p
              join pg_namespace n on n.oid = p.pronamespace
              where n.nspname = 'public' and p.proname = $1
            )
            """,
            name,
        )
    )


async def verify_schema(pool: asyncpg.Pool, settings: Settings) -> None:
    """Run every startup guard against the pool's first connection."""
    async with pool.acquire() as conn:
        if not await _function_exists(conn, "embedding_dim"):
            # A checkout that has not run `make db-reset` yet. Refusing to boot
            # here would be a worse failure than a missing guard.
            log.warning(
                "embedding dimension guard skipped: public.embedding_dim() is absent "
                "(migrations not applied?)"
            )
            return
        await assert_embedding_dimension(conn, settings)
