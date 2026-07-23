import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)

# Above this, we log a warning naming the slow stage — lets us tell apart
# "DB/session was slow" from "something else in the handler was slow"
# without guessing from the outer aiogram.event duration alone.
_SLOW_THRESHOLD_S = 0.5


class DatabaseMiddleware(BaseMiddleware):
    """Middleware that automatically provides a DB session to every handler."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        acquire_start = time.monotonic()
        async with self.session_factory() as session:
            acquire_ms = (time.monotonic() - acquire_start) * 1000
            data["session"] = session
            handler_start = time.monotonic()
            try:
                result = await handler(event, data)
                handler_ms = (time.monotonic() - handler_start) * 1000
                commit_start = time.monotonic()
                await session.commit()
                commit_ms = (time.monotonic() - commit_start) * 1000
            except Exception:
                await session.rollback()
                raise
            total_s = acquire_ms / 1000 + handler_ms / 1000 + commit_ms / 1000
            if total_s > _SLOW_THRESHOLD_S:
                logger.warning(
                    "Slow update: acquire_session=%.0fms handler=%.0fms commit=%.0fms",
                    acquire_ms,
                    handler_ms,
                    commit_ms,
                )
            return result
