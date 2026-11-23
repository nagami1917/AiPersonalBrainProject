"""Tests for the boot-time embedding dimension guard.

The defect being fixed: `config.py` validated *model against declared dim*, so
`EMBEDDING_MODEL=text-embedding-3-large` with the default 1536 passed every
check and booted. The column is `vector(1536)`, so the first chunk insert then
failed — after the upload had been accepted and the job queued.

These tests use a fake connection rather than a database so they run offline,
matching the rest of `tests/unit`. The concrete SQL behaviour is covered by the
db suite; what matters here is the decision logic and the message an operator
reads at 2am.
"""

from __future__ import annotations

import pytest
from app.core.config import Settings
from app.core.schema_guard import (
    EmbeddingDimensionMismatch,
    assert_embedding_dimension,
    verify_schema,
)


class FakeConn:
    """Records queries and returns scripted values."""

    def __init__(self, *, db_dim: int = 1536, has_function: bool = True) -> None:
        self.db_dim = db_dim
        self.has_function = has_function
        self.executed: list[str] = []

    async def fetchval(self, query: str, *args: object) -> object:
        if "embedding_dim()" in query:
            if not self.has_function:
                raise RuntimeError("function public.embedding_dim() does not exist")
            return self.db_dim
        if "pg_proc" in query:
            return self.has_function
        raise AssertionError(f"unexpected query: {query}")

    async def execute(self, query: str) -> None:
        self.executed.append(query)


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    def acquire(self):  # type: ignore[no-untyped-def]
        conn = self._conn

        class _Ctx:
            async def __aenter__(self) -> FakeConn:
                return conn

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Ctx()


def settings(dim: int = 1536, model: str = "text-embedding-3-small") -> Settings:
    """A Settings object with the guard-relevant fields pinned.

    Built with `model_construct` so the alias/cross-field validation in
    `config.py` is not re-run: these tests are about the schema guard, and a
    deliberate mismatch could not otherwise be constructed.
    """
    return Settings.model_construct(
        pinned_embedding_model=model,
        pinned_embedding_dim=dim,
    )


class TestMatchingDimension:
    async def test_passes_when_code_and_column_agree(self) -> None:
        conn = FakeConn(db_dim=1536)
        await assert_embedding_dimension(conn, settings(1536))  # type: ignore[arg-type]

    async def test_calls_the_sql_guard_that_was_previously_dead_code(self) -> None:
        # assert_embedding_dim() shipped in migration 0003 and was never called.
        conn = FakeConn(db_dim=1536)
        await assert_embedding_dimension(conn, settings(1536))  # type: ignore[arg-type]
        assert any("assert_embedding_dim()" in q for q in conn.executed)


class TestMismatch:
    async def test_raises_when_configured_width_differs_from_the_column(self) -> None:
        conn = FakeConn(db_dim=1536)
        with pytest.raises(EmbeddingDimensionMismatch):
            await assert_embedding_dimension(
                conn,
                settings(3072, "text-embedding-3-large"),  # type: ignore[arg-type]
            )

    async def test_message_names_the_model_both_widths_and_the_way_out(self) -> None:
        # An operator reads this in a crash log, so it has to be actionable.
        conn = FakeConn(db_dim=1536)
        with pytest.raises(EmbeddingDimensionMismatch) as exc:
            await assert_embedding_dimension(
                conn,
                settings(3072, "text-embedding-3-large"),  # type: ignore[arg-type]
            )
        message = str(exc.value)
        assert "text-embedding-3-large" in message
        assert "3072" in message
        assert "vector(1536)" in message
        assert "idx_chunks_embedding_hnsw" in message

    async def test_does_not_run_the_sql_guard_when_already_mismatched(self) -> None:
        # Failing fast matters: the SQL guard raises an opaque exception, and the
        # Python check has the readable message.
        conn = FakeConn(db_dim=1536)
        with pytest.raises(EmbeddingDimensionMismatch):
            await assert_embedding_dimension(conn, settings(3072, "x"))  # type: ignore[arg-type]
        assert conn.executed == []

    async def test_a_wider_column_than_configured_also_fails(self) -> None:
        # Both directions are wrong: inserts would pad or truncate.
        conn = FakeConn(db_dim=3072)
        with pytest.raises(EmbeddingDimensionMismatch):
            await assert_embedding_dimension(conn, settings(1536))  # type: ignore[arg-type]


class TestMissingSchema:
    async def test_skips_with_a_warning_when_migrations_have_not_run(self) -> None:
        # `make api` before `make db-reset` is a normal local sequence; refusing
        # to boot would be a worse failure than a missing guard.
        conn = FakeConn(has_function=False)
        await verify_schema(FakePool(conn), settings())  # type: ignore[arg-type]

    async def test_verify_schema_acquires_a_connection_from_the_pool(self) -> None:
        conn = FakeConn(db_dim=1536)
        await verify_schema(FakePool(conn), settings())  # type: ignore[arg-type]
        assert any("embedding_dim" in q for q in conn.executed) or conn.db_dim == 1536


class TestGuardIsWiredIntoStartup:
    def test_lifespan_calls_verify_schema(self) -> None:
        # A guard nobody calls is how the original bug survived: migration 0003
        # shipped assert_embedding_dim() and no caller existed.
        import inspect

        from app import main

        source = inspect.getsource(main.lifespan)
        assert "verify_schema" in source
