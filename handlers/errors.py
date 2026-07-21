import logging
from asyncio import TimeoutError

from aiogram import Router
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import ErrorEvent, Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database.crud import get_user_language
from locales.texts import DEFAULT_LANG, get_text

router = Router()
logger = logging.getLogger(__name__)


@router.errors()
async def global_error_handler(event: ErrorEvent, session: AsyncSession | None = None) -> None:
    """Global handler for all exceptions."""
    exception = event.exception
    event_type = event.update.event_type
    if isinstance(exception, TelegramRetryAfter):
        logger.warning("Telegram rate limit for %s; retry after %s seconds", event_type, exception.retry_after)
        return
    if isinstance(exception, TelegramForbiddenError):
        logger.info("Telegram denied delivery for %s (the user likely blocked the bot)", event_type)
        return
    if isinstance(exception, TelegramBadRequest):
        logger.warning("Invalid Telegram request for %s: %s", event_type, exception)
        return
    if isinstance(exception, (TelegramNetworkError, TelegramServerError, TimeoutError)):
        logger.warning("Temporary transport failure for %s: %s", event_type, exception, exc_info=True)
    elif isinstance(exception, SQLAlchemyError):
        logger.error("Database failure while handling %s", event_type, exc_info=True)
    else:
        logger.error("Critical error triggered by %s: %s", event_type, exception, exc_info=True)

    user_id: int | None = None
    if event.update.message and event.update.message.from_user:
        user_id = event.update.message.from_user.id
    elif event.update.callback_query and event.update.callback_query.from_user:
        user_id = event.update.callback_query.from_user.id

    language = DEFAULT_LANG
    if session is not None and user_id is not None:
        try:
            language = await get_user_language(session, user_id)
        except Exception:
            language = DEFAULT_LANG

    error_text = get_text(language, "generic_error")

    try:
        if event.update.message:
            await event.update.message.answer(error_text)
        elif event.update.callback_query:
            message = event.update.callback_query.message
            if isinstance(message, Message):
                await message.answer(error_text)
            else:
                await event.update.callback_query.answer(error_text, show_alert=True)
    except TelegramAPIError:
        logger.debug("Could not deliver the error message", exc_info=True)
