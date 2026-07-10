import logging
from aiogram import Router
from aiogram.types import ErrorEvent, Message
from aiogram.exceptions import TelegramAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from database.crud import get_user_language
from locales.texts import get_text

router = Router()
logger = logging.getLogger(__name__)


@router.errors()
async def global_error_handler(event: ErrorEvent, session: AsyncSession | None = None) -> None:
    """Global handler for all exceptions."""
    logger.error(f"Critical error triggered by {event.update.event_type}: {event.exception}", exc_info=True)

    user_id: int | None = None
    if event.update.message and event.update.message.from_user:
        user_id = event.update.message.from_user.id
    elif event.update.callback_query and event.update.callback_query.from_user:
        user_id = event.update.callback_query.from_user.id

    language = "ua"
    if session is not None and user_id is not None:
        try:
            language = await get_user_language(session, user_id)
        except Exception:
            language = "ua"

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
        pass
