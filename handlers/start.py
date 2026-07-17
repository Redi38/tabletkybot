import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from sqlalchemy.ext.asyncio import AsyncSession

from database.crud import get_or_create_user, update_user_language
from locales.texts import btn_variants, get_text

router = Router()
logger = logging.getLogger(__name__)


def get_main_keyboard(language: str = "ua") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=get_text(language, "btn_medicines")),
                KeyboardButton(text=get_text(language, "btn_prescriptions")),
            ],
            [KeyboardButton(text=get_text(language, "btn_settings"))],
        ],
        resize_keyboard=True,
        input_field_placeholder=get_text(language, "btn_placeholder"),
    )


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not message.from_user:
        return
    await state.clear()

    user = await get_or_create_user(
        session,
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )
    language = user.language or "ua"

    logger.info(f"User {message.from_user.id} (@{message.from_user.username}) started the bot")

    await message.answer(
        get_text(language, "start_text", name=user.full_name),
        reply_markup=get_main_keyboard(language),
        parse_mode="HTML",
    )


@router.message(Command("help"))
@router.message(F.text.in_(btn_variants("btn_help")))
async def cmd_help(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return
    user = await get_or_create_user(
        session,
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )
    language = user.language or "ua"
    logger.info(f"User {message.from_user.id} (@{message.from_user.username}) requested /help")
    await message.answer(
        get_text(language, "help_text"),
        parse_mode="HTML",
        reply_markup=get_main_keyboard(language),
    )


@router.callback_query(F.data.startswith("lang_"))
async def set_language(call: CallbackQuery, session: AsyncSession) -> None:
    if not call.from_user or not call.data or not isinstance(call.message, Message):
        return
    language = call.data.split("_", 1)[1]
    await get_or_create_user(
        session,
        call.from_user.id,
        call.from_user.username,
        call.from_user.full_name,
    )
    await update_user_language(session, call.from_user.id, language)
    logger.info(f"User {call.from_user.id} (@{call.from_user.username}) changed language to '{language}'")
    await call.message.answer(get_text(language, "lang_changed"), reply_markup=get_main_keyboard(language))
    await call.answer()
