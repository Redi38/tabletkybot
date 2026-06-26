import logging
from aiogram import Router
from aiogram.types import ErrorEvent, Message
from aiogram.exceptions import TelegramAPIError

router = Router()
logger = logging.getLogger(__name__)

@router.errors()
async def global_error_handler(event: ErrorEvent):
    """Глобальний обробник усіх винятків."""
    logger.error(f"Critical error triggered by {event.update.event_type}: {event.exception}", exc_info=True)

    try:
        if event.update.message:
            await event.update.message.answer("⚠️ Сталася помилка під час обробки вашого запиту.")
        elif event.update.callback_query:
            message = event.update.callback_query.message
            if isinstance(message, Message):
                await message.answer("⚠️ Сталася помилка.")
            else:
                await event.update.callback_query.answer("⚠️ Сталася помилка.", show_alert=True)
    except TelegramAPIError:
        pass