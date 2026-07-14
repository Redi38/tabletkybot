import os

from cryptography.fernet import Fernet

os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Base

scheduler_module = importlib.import_module("services.scheduler")


@pytest.fixture(autouse=True)
def _reset_scheduler_state():
    """
    scheduler.py keeps module-level global state (the APScheduler instance,
    _redis_client, _manual_reminder_today). Without resetting these between
    tests, jobs and mocked clients leak across test cases and cause
    order-dependent failures.
    """
    yield
    for job in list(scheduler_module.scheduler.get_jobs()):
        scheduler_module.scheduler.remove_job(job.id)
    scheduler_module._manual_reminder_today.clear()
    scheduler_module._redis_client = None


@pytest.fixture
def mock_redis():
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.set = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=1)

    async def _empty_scan_iter(match=None):
        for key in []:
            yield key

    client.scan_iter = _empty_scan_iter
    scheduler_module._redis_client = client
    return client


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.delete_message = AsyncMock()
    return bot


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()
