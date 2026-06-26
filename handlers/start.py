from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup,
    KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton,
)
from sqlalchemy.ext.asyncio import AsyncSession
from database.crud import get_or_create_user, update_user_language, get_user_language
from locales.texts import get_text, TEXTS

router = Router()

def get_main_keyboard(language: str = "uk") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=get_text(language, "btn_medicines")),
             KeyboardButton(text=get_text(language, "btn_report"))],
            [KeyboardButton(text=get_text(language, "btn_ai")),
             KeyboardButton(text=get_text(language, "btn_lang"))],
            [KeyboardButton(text=get_text(language, "btn_settings")),
             KeyboardButton(text=get_text(language, "btn_help"))],
        ],
        resize_keyboard=True,
        input_field_placeholder=get_text(language, "btn_placeholder"),
    )

def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Українська", callback_data="lang_uk", style="primary"),
        InlineKeyboardButton(text="English", callback_data="lang_en", style="primary"),
    ]])

@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, state: FSMContext) -> None:
    if not message.from_user:
        return
    await state.clear()
    user = await get_or_create_user(
        session, message.from_user.id,
        message.from_user.username, message.from_user.full_name,
    )
    language = user.language or "uk"
    await message.answer(
        get_text(language, "start_text", name=user.full_name),
        reply_markup=get_main_keyboard(language),
        parse_mode="HTML",
    )

@router.message(Command("help"))
@router.message(F.text.in_({TEXTS["uk"]["btn_help"], TEXTS["en"]["btn_help"]}))
async def cmd_help(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return
    user = await get_or_create_user(
        session, message.from_user.id,
        message.from_user.username, message.from_user.full_name,
    )
    language = user.language or "uk"
    await message.answer(
        get_text(language, "help_text"),
        parse_mode="HTML",
        reply_markup=get_main_keyboard(language),
    )

@router.message(F.text.in_({TEXTS["uk"]["btn_lang"], TEXTS["en"]["btn_lang"]}))
async def choose_language(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return
    language = await get_user_language(session, message.from_user.id)
    await message.answer(get_text(language, "lang_choose"), reply_markup=language_keyboard())

@router.callback_query(F.data.in_({"lang_uk", "lang_en"}))
async def set_language(call: CallbackQuery, session: AsyncSession) -> None:
    if not call.from_user or not call.data or not isinstance(call.message, Message):
        return
    language = call.data.split("_", 1)[1]
    await get_or_create_user(
        session, call.from_user.id,
        call.from_user.username, call.from_user.full_name,
    )
    await update_user_language(session, call.from_user.id, language)
    await call.message.answer(get_text(language, "lang_changed"), reply_markup=get_main_keyboard(language))
    await call.answer()

@router.message(F.text.startswith("/") == False)
async def fallback_handler(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.from_user:
        return
    if await state.get_state() is None:
        user = await get_or_create_user(
            session, message.from_user.id,
            message.from_user.username, message.from_user.full_name,
        )
        language = user.language or "uk"
        await message.answer(get_text(language, "fallback_text"), reply_markup=get_main_keyboard(language))