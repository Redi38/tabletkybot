"""Tests for the internal /health endpoint (web/internal_api.py::build_health_handler)."""

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import web.internal_api as main_module
from database.models import Base


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _scheduler_running(value: bool):
    # `running` is a read-only property on AsyncIOScheduler's class, not a
    # plain instance attribute, so it has to be patched at the class level.
    return patch.object(type(main_module.scheduler), "running", new_callable=PropertyMock, return_value=value)


async def _get_health(session_factory, redis_url="redis://localhost:6379/0"):
    handler = main_module.build_health_handler(session_factory, redis_url)
    app = web.Application()
    app.router.add_get("/health", handler)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/health")
        body = await resp.json()
        return resp.status, body


@pytest.mark.asyncio
async def test_returns_200_when_db_redis_and_scheduler_are_all_ok(session_factory):
    fake_redis = AsyncMock()
    fake_redis.ping = AsyncMock()
    fake_redis.close = AsyncMock()

    with patch.object(main_module.aioredis, "from_url", return_value=fake_redis):
        with _scheduler_running(True):
            status, body = await _get_health(session_factory)

    assert status == 200
    assert body["status"] == "healthy"
    assert body["checks"] == {"database": "ok", "redis": "ok", "scheduler": "running"}


@pytest.mark.asyncio
async def test_returns_503_when_scheduler_is_stopped(session_factory):
    fake_redis = AsyncMock()
    fake_redis.ping = AsyncMock()
    fake_redis.close = AsyncMock()

    with patch.object(main_module.aioredis, "from_url", return_value=fake_redis):
        with _scheduler_running(False):
            status, body = await _get_health(session_factory)

    assert status == 503
    assert body["status"] == "unhealthy"
    assert body["checks"]["scheduler"] == "stopped"


@pytest.mark.asyncio
async def test_returns_503_when_redis_ping_fails(session_factory):
    fake_redis = AsyncMock()
    fake_redis.ping = AsyncMock(side_effect=ConnectionError("redis unreachable"))
    fake_redis.close = AsyncMock()

    with patch.object(main_module.aioredis, "from_url", return_value=fake_redis):
        with _scheduler_running(True):
            status, body = await _get_health(session_factory)

    assert status == 503
    assert body["status"] == "unhealthy"
    assert "error" in body["checks"]["redis"]
    # the DB and scheduler checks should be unaffected by the redis failure
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["scheduler"] == "running"


@pytest.mark.asyncio
async def test_returns_503_when_db_query_fails(session_factory):
    fake_redis = AsyncMock()
    fake_redis.ping = AsyncMock()
    fake_redis.close = AsyncMock()

    broken_session_factory = MagicMock(side_effect=RuntimeError("db connection refused"))

    with patch.object(main_module.aioredis, "from_url", return_value=fake_redis):
        with _scheduler_running(True):
            status, body = await _get_health(broken_session_factory)

    assert status == 503
    assert "error" in body["checks"]["database"]


@pytest.mark.asyncio
async def test_all_three_checks_failing_still_returns_a_single_503(session_factory):
    fake_redis = AsyncMock()
    fake_redis.ping = AsyncMock(side_effect=ConnectionError("redis down"))
    fake_redis.close = AsyncMock()
    broken_session_factory = MagicMock(side_effect=RuntimeError("db down"))

    with patch.object(main_module.aioredis, "from_url", return_value=fake_redis):
        with _scheduler_running(False):
            status, body = await _get_health(broken_session_factory)

    assert status == 503
    assert body["status"] == "unhealthy"
    assert "error" in body["checks"]["database"]
    assert "error" in body["checks"]["redis"]
    assert body["checks"]["scheduler"] == "stopped"
