from config import Config
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup,
    KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton,
)
from sqlalchemy.ext.asyncio import AsyncSession
from database.crud import get_or_create_user, update_user_language, get_user_language
from database import crud
from locales.texts import get_text, TEXTS, btn_variants
from services.ai_service import get_ai_agent_response

router = Router()


def get_main_keyboard(language: str = "uk") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=get_text(language, "btn_medicines")),
             KeyboardButton(text=get_text(language, "btn_report"))],
            [KeyboardButton(text=get_text(language, "btn_ai")),
             KeyboardButton(text=get_text(language, "btn_lang"))],
            [KeyboardButton(text=get_text(language, "btn_settings")),
             KeyboardButton(text=get_text(language, "btn_prescriptions"))],
        ],
        resize_keyboard=True,
        input_field_placeholder=get_text(language, "btn_placeholder"),
    )


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Українська", callback_data="lang_uk", style="primary"),
        InlineKeyboardButton(text="English", callback_data="lang_en", style="primary"),
        InlineKeyboardButton(text="Русский", callback_data="lang_ru", style="primary"),
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
@router.message(F.text.in_(btn_variants("btn_help")))
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


@router.message(F.text.in_(btn_variants("btn_lang")))
async def choose_language(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return
    language = await get_user_language(session, message.from_user.id)
    await message.answer(get_text(language, "lang_choose"), reply_markup=language_keyboard())


@router.callback_query(F.data.startswith("lang_"))
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
async def fallback_handler(
        message: Message, state: FSMContext, session: AsyncSession,
        config: Config, bot: Bot,
) -> None:
    if not message.from_user or not message.text:
        return
    if await state.get_state() is not None:
        return  # юзер посеред іншого FSM-флоу — не заважаємо

    user = await get_or_create_user(
        session, message.from_user.id,
        message.from_user.username, message.from_user.full_name,
    )
    language = user.language or "uk"

    await bot.send_chat_action(message.chat.id, "typing")

    history = await crud.get_chat_history(session, message.from_user.id, limit=10)
    if history and not isinstance(history[0], dict):
        conv_messages = [{"role": m.role, "content": m.content} for m in history]
    else:
        conv_messages = list(history)
    conv_messages.append({"role": "user", "content": message.text})

    response_text, model_used = await get_ai_agent_response(
        config, session, message.from_user.id, conv_messages, language=language,
    )

    await crud.add_chat_message(session, message.from_user.id, "user", message.text)
    await crud.add_chat_message(session, message.from_user.id, "assistant", response_text)

    formatted = f"{response_text}\n\n<i>🔮 {get_text(language, 'ai_model')}: {model_used}</i>"
    await message.answer(formatted, parse_mode="HTML", reply_markup=get_main_keyboard(language))
