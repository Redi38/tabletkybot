"""
Tests for database/db.py::init_db.

init_db() creates a real asyncpg-pooled engine (pool_size, max_overflow,
etc. are Postgres-only options — not supported by sqlite/aiosqlite, which
the rest of the test suite uses for db_session), so we mock
create_async_engine/async_sessionmaker rather than pointing this at a
real database. The goal is to verify the wiring: the engine is built
with the given URL and expected pool settings, tables are created via
Base.metadata.create_all through run_sync, and the resulting
session_factory is returned to the caller.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from database.db import _create_tables, init_db
from database.models import Base


def _mock_engine():
    conn = MagicMock()
    conn.run_sync = AsyncMock()

    engine = MagicMock()
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=conn)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    engine.begin = MagicMock(return_value=begin_cm)

    return engine, conn


class TestCreateTables:
    def test_calls_metadata_create_all_with_the_given_connection(self):
        sync_conn = MagicMock()
        with patch.object(Base.metadata, "create_all") as mock_create_all:
            _create_tables(sync_conn)

        mock_create_all.assert_called_once_with(sync_conn)


class TestInitDb:
    async def test_creates_engine_with_the_given_url(self):
        engine, _ = _mock_engine()
        with (
            patch("database.db.create_async_engine", MagicMock(return_value=engine)) as mock_create_engine,
            patch("database.db.async_sessionmaker", MagicMock()),
        ):
            await init_db("postgresql+asyncpg://user:pass@host/db")

        args, kwargs = mock_create_engine.call_args
        assert args[0] == "postgresql+asyncpg://user:pass@host/db"

    async def test_configures_expected_pool_settings(self):
        engine, _ = _mock_engine()
        with (
            patch("database.db.create_async_engine", MagicMock(return_value=engine)) as mock_create_engine,
            patch("database.db.async_sessionmaker", MagicMock()),
        ):
            await init_db("postgresql+asyncpg://user:pass@host/db")

        _, kwargs = mock_create_engine.call_args
        assert kwargs["echo"] is False
        assert kwargs["pool_size"] == 10
        assert kwargs["max_overflow"] == 20
        assert kwargs["pool_pre_ping"] is True
        assert kwargs["pool_recycle"] == 3600

    async def test_creates_tables_via_run_sync(self):
        engine, conn = _mock_engine()
        with (
            patch("database.db.create_async_engine", MagicMock(return_value=engine)),
            patch("database.db.async_sessionmaker", MagicMock()),
        ):
            await init_db("postgresql+asyncpg://user:pass@host/db")

        conn.run_sync.assert_awaited_once_with(_create_tables)

    async def test_returns_the_session_factory(self):
        engine, _ = _mock_engine()
        sentinel_factory = MagicMock()
        with (
            patch("database.db.create_async_engine", MagicMock(return_value=engine)),
            patch("database.db.async_sessionmaker", MagicMock(return_value=sentinel_factory)) as mock_sessionmaker,
        ):
            result = await init_db("postgresql+asyncpg://user:pass@host/db")

        assert result is sentinel_factory
        _, kwargs = mock_sessionmaker.call_args
        assert kwargs["bind"] is engine
        assert kwargs["expire_on_commit"] is False
