"""
Shared health-check helpers for the two separate /health endpoints in this
project (admin/app.py's FastAPI route and web/internal_api.py's aiohttp
handler). Both need to verify DB and Redis connectivity the same way.
"""

import redis.asyncio as aioredis
from sqlalchemy import text


async def check_database(session_factory) -> str:
    """Runs a trivial query through session_factory() to verify DB connectivity.

    Returns "ok" on success, or "error: <message>" on failure. Never raises.
    """
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        return "ok"
    except Exception as e:
        return f"error: {e}"


async def check_redis(redis_url: str) -> str:
    """Pings Redis at redis_url to verify connectivity.

    Returns "ok" on success, or "error: <message>" on failure. Never raises.
    """
    try:
        redis_client = aioredis.from_url(redis_url)
        await redis_client.ping()
        await redis_client.close()
        return "ok"
    except Exception as e:
        return f"error: {e}"
