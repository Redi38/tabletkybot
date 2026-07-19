import os

from cryptography.fernet import Fernet

os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Base
from services.scheduler import jobs as scheduler_jobs_module
from services.scheduler import redis_state as scheduler_redis_module


@pytest.fixture(autouse=True)
def _reset_scheduler_state():
    """
    The scheduler package keeps module-level global state (the APScheduler
    instance and _manual_reminder_today in jobs.py, _redis_client in
    redis_state.py). Without resetting these between tests, jobs and mocked
    clients leak across test cases and cause order-dependent failures.
    """
    yield
    for job in list(scheduler_jobs_module.scheduler.get_jobs()):
        scheduler_jobs_module.scheduler.remove_job(job.id)
    scheduler_jobs_module._manual_reminder_today.clear()
    scheduler_redis_module._redis_client = None


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
    scheduler_redis_module._redis_client = client
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
