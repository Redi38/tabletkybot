import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, Iterator

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

# Holds the current Telegram update_id (or a short fallback tag) for the
# duration of processing a single update. Every log line emitted while
# handling that update will automatically include it, without having to
# thread an id through every function call manually.
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")

_action_logger = logging.getLogger("bot.actions")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.cid = _correlation_id.get()
        return True


class CorrelationIdMiddleware(BaseMiddleware):
    """
    Sets a correlation id (the Telegram update_id) for the duration of
    processing one Update.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update_id = event.update_id if isinstance(event, Update) else None
        tag = f"upd:{update_id}" if update_id is not None else "upd:-"
        token = _correlation_id.set(tag)
        try:
            return await handler(event, data)
        finally:
            _correlation_id.reset(token)


class ActionLoggingMiddleware(BaseMiddleware):
    """
    Logs a one-line summary of *every* callback query (button press) and
    text message that reaches the dispatcher — regardless of whether the
    handler that ends up processing it calls logger.info itself.
    """

    _MAX_TEXT_LEN = 80

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update):
            if event.callback_query is not None:
                self._log_callback(event.callback_query)
            elif event.message is not None:
                self._log_message(event.message)
        return await handler(event, data)

    def _log_callback(self, call: CallbackQuery) -> None:
        user = call.from_user
        who = f"{user.id} (@{user.username})" if user else "unknown"
        _action_logger.info(f"[button] user={who} data={call.data!r}")

    def _log_message(self, message: Message) -> None:
        user = message.from_user
        who = f"{user.id} (@{user.username})" if user else "unknown"
        text = message.text or message.caption or ("<voice>" if message.voice else "<non-text>")
        if len(text) > self._MAX_TEXT_LEN:
            text = text[: self._MAX_TEXT_LEN] + "…"
        _action_logger.info(f"[message] user={who} text={text!r}")


def get_correlation_id() -> str:
    """Reads the current correlation id."""
    return _correlation_id.get()


@contextmanager
def correlation_scope(tag: str) -> Iterator[None]:
    """
    Manually tags every log line for the duration of a `with` block, for
    code paths that don't go through the aiogram dispatcher.
    """
    token = _correlation_id.set(tag)
    try:
        yield
    finally:
        _correlation_id.reset(token)
