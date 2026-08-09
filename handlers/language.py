import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database.crud import get_or_create_user, update_user_language
from handlers.start import get_main_keyboard
from locales.texts import DEFAULT_LANG, get_text

router = Router()
logger = logging.getLogger(__name__)


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇺🇦 Українська", callback_data="lang_ua", style="primary"),
                InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en", style="primary"),
                InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru", style="primary"),
            ]
        ]
    )


@router.callback_query(F.data.startswith("lang_"))
async def set_language(call: CallbackQuery, session: AsyncSession) -> None:
    if not call.from_user or not call.data or not isinstance(call.message, Message):
        return
    language = call.data.split("_", 1)[1]
    user = await get_or_create_user(
        session,
        call.from_user.id,
        call.from_user.username,
        call.from_user.full_name,
    )

    if (user.language or DEFAULT_LANG) == language:
        await call.answer(get_text(language, "lang_already_selected"), show_alert=True)
        return

    await update_user_language(session, call.from_user.id, language)
    logger.info(f"User {call.from_user.id} (@{call.from_user.username}) changed language to '{language}'")

    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.warning(f"Failed to delete the language-selection message for user {call.from_user.id}: {e}")

    await call.message.answer(get_text(language, "lang_changed"), reply_markup=get_main_keyboard(language))
    await call.answer()
