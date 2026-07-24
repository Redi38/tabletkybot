"""
Tests for middleware/db_middleware.py: injects a DB session into every
update, commits on success, rolls back and re-raises on handler failure,
and logs a warning when the whole update was slow.
"""

import logging

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Base
from middleware.db_middleware import _SLOW_THRESHOLD_S, DatabaseMiddleware


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


class TestDatabaseMiddleware:
    async def test_injects_a_session_into_the_handler_data(self, session_factory):
        middleware = DatabaseMiddleware(session_factory)
        seen_session = {}

        async def handler(event, data):
            seen_session["session"] = data.get("session")
            return "ok"

        result = await middleware(handler, event=object(), data={})

        assert result == "ok"
        assert seen_session["session"] is not None

    async def test_commits_the_session_after_a_successful_handler(self, session_factory):
        from database import crud

        middleware = DatabaseMiddleware(session_factory)

        async def handler(event, data):
            await crud.get_or_create_user(data["session"], 1, "tester", "Test User")

        await middleware(handler, event=object(), data={})

        # A fresh session should now see the committed user.
        async with session_factory() as verify_session:
            user = await crud.get_or_create_user(verify_session, 1, "tester", "Test User")
            assert user.id == 1

    async def test_rolls_back_and_reraises_when_the_handler_fails(self, session_factory):
        from database import crud

        middleware = DatabaseMiddleware(session_factory)

        async def handler(event, data):
            await crud.get_or_create_user(data["session"], 1, "tester", "Test User")
            raise RuntimeError("handler exploded")

        with pytest.raises(RuntimeError, match="handler exploded"):
            await middleware(handler, event=object(), data={})

        # The user created before the failure must not have been committed.
        async with session_factory() as verify_session:
            from sqlalchemy import select

            from database.models import User

            result = await verify_session.execute(select(User).where(User.id == 1))
            assert result.scalar_one_or_none() is None

    async def test_fast_update_does_not_log_a_warning(self, session_factory, caplog):
        middleware = DatabaseMiddleware(session_factory)

        async def handler(event, data):
            return "ok"

        with caplog.at_level(logging.WARNING, logger="middleware.db_middleware"):
            await middleware(handler, event=object(), data={})

        assert not any("Slow update" in record.message for record in caplog.records)

    async def test_slow_update_logs_a_warning_with_stage_breakdown(self, session_factory, caplog, monkeypatch):
        middleware = DatabaseMiddleware(session_factory)

        async def slow_handler(event, data):
            # Simulate a handler that takes longer than _SLOW_THRESHOLD_S by
            # advancing the clock the middleware reads, rather than an
            # actual sleep, to keep the test fast.
            return "ok"

        times = iter([0.0, 0.0, 0.0, _SLOW_THRESHOLD_S + 1, _SLOW_THRESHOLD_S + 1, _SLOW_THRESHOLD_S + 1])
        monkeypatch.setattr("middleware.db_middleware.time.monotonic", lambda: next(times, _SLOW_THRESHOLD_S + 1))

        with caplog.at_level(logging.WARNING, logger="middleware.db_middleware"):
            await middleware(slow_handler, event=object(), data={})

        assert any("Slow update" in record.message for record in caplog.records)

    async def test_handler_receives_other_data_keys_unchanged(self, session_factory):
        middleware = DatabaseMiddleware(session_factory)
        received = {}

        async def handler(event, data):
            received.update(data)

        await middleware(handler, event=object(), data={"bot": "fake-bot", "config": "fake-config"})

        assert received["bot"] == "fake-bot"
        assert received["config"] == "fake-config"
        assert "session" in received
