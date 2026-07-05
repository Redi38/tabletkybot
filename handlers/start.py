import logging
import aiohttp
from config import Config
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup,
    KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession
from database.crud import get_or_create_user, update_user_language, get_user_language
from database import crud
from locales.texts import get_text, TEXTS, btn_variants
from services.ai_service import get_ai_agent_response, get_ai_vision_response

router = Router()
logger = logging.getLogger(__name__)


def get_main_keyboard(language: str = "ua") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=get_text(language, "btn_medicines")),
             KeyboardButton(text=get_text(language, "btn_report"))],
            [KeyboardButton(text=get_text(language, "btn_lang")),
             KeyboardButton(text=get_text(language, "btn_settings"))],
            [KeyboardButton(text=get_text(language, "btn_prescriptions"))],
        ],
        resize_keyboard=True,
        input_field_placeholder=get_text(language, "btn_placeholder"),
    )


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Українська", callback_data="lang_ua", style="primary"),
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
    language = user.language or "ua"
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
    language = user.language or "ua"
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


async def download_telegram_file(bot: Bot, file_id: str) -> bytes:
    file = await bot.get_file(file_id)
    file_url = f"https://api.telegram.org/file/bot{bot.token}/{file.file_path}"
    async with aiohttp.ClientSession() as session:
        async with session.get(file_url) as resp:
            resp.raise_for_status()
            return await resp.read()


async def pdf_to_image(pdf_bytes: bytes) -> bytes | None:
    """Конвертувати першу сторінку PDF у зображення."""
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page = doc[0]
        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)
        return pix.tobytes("jpeg")
    except Exception as e:
        logger.error(f"Помилка конвертації PDF: {e}")
        return None


async def _send_ai_answer(message: Message, response_text: str, model_used: str, language: str) -> None:
    formatted = f"{response_text}\n\n<i>🔮 {get_text(language, 'ai_model')}: {model_used}</i>"
    try:
        await message.answer(formatted, parse_mode="HTML", reply_markup=get_main_keyboard(language))
    except TelegramBadRequest as e:
        if "can't parse entities" in str(e).lower():
            logger.warning(f"Згенеровано невалідний HTML. Відправляємо сирий текст. Помилка: {e}")
            await message.answer(formatted, parse_mode=None, reply_markup=get_main_keyboard(language))
        else:
            raise


@router.message(F.photo)
async def handle_photo(message: Message, session: AsyncSession, config: Config, bot: Bot, state: FSMContext) -> None:
    """Обробка фото через Vision модель — працює в звичайному чаті, без окремого AI-режиму."""
    if not message.from_user or not message.photo:
        return
    if await state.get_state() is not None:
        return

    await bot.send_chat_action(message.chat.id, "typing")

    user = await get_or_create_user(
        session, message.from_user.id,
        message.from_user.username, message.from_user.full_name,
    )
    language = user.language or "u"

    photo = message.photo[-1]
    caption = message.caption or get_text(language, "ai_analyze_photo")

    await message.answer(get_text(language, "ai_analyzing"), reply_markup=get_main_keyboard(language))

    try:
        image_bytes = await download_telegram_file(bot, photo.file_id)
        response_text, model_used = await get_ai_vision_response(config, image_bytes, caption, language)

        await crud.add_chat_message(session, message.from_user.id, "user", f"[Фото] {caption}")
        await crud.add_chat_message(session, message.from_user.id, "assistant", response_text)
        await _send_ai_answer(message, response_text, model_used, language)

    except Exception as e:
        logger.error(f"Помилка обробки фото AI: {e}")
        await message.answer(get_text(language, "ai_err_photo"), reply_markup=get_main_keyboard(language))


@router.message(F.document)
async def handle_document(message: Message, session: AsyncSession, config: Config, bot: Bot, state: FSMContext) -> None:
    """Обробка PDF документів — працює в звичайному чаті, без окремого AI-режиму."""
    doc = message.document
    if not message.from_user or not doc:
        return
    if await state.get_state() is not None:
        return

    user = await get_or_create_user(
        session, message.from_user.id,
        message.from_user.username, message.from_user.full_name,
    )
    language = user.language or "ua"

    if doc.mime_type != "application/pdf":
        await message.answer(
            get_text(language, "ai_err_type", mime=str(doc.mime_type)),
            parse_mode="HTML",
            reply_markup=get_main_keyboard(language),
        )
        return

    await bot.send_chat_action(message.chat.id, "typing")
    await message.answer(get_text(language, "ai_process_pdf"), reply_markup=get_main_keyboard(language))

    try:
        pdf_bytes = await download_telegram_file(bot, doc.file_id)
        image_bytes = await pdf_to_image(pdf_bytes)

        if image_bytes is None:
            await message.answer(
                get_text(language, "ai_err_pdf_lib"),
                parse_mode="HTML",
                reply_markup=get_main_keyboard(language),
            )
            return

        caption = message.caption or get_text(language, "ai_analyze_pdf")
        response_text, model_used = await get_ai_vision_response(config, image_bytes, caption, language)

        await crud.add_chat_message(session, message.from_user.id, "user", f"[PDF] {caption}")
        await crud.add_chat_message(session, message.from_user.id, "assistant", response_text)
        await _send_ai_answer(message, response_text, model_used, language)

    except Exception as e:
        logger.error(f"Помилка обробки PDF документа: {e}")
        await message.answer(get_text(language, "ai_err_pdf"), reply_markup=get_main_keyboard(language))


@router.message(F.text.startswith("/") == False)
async def fallback_handler(
        message: Message, state: FSMContext, session: AsyncSession,
        config: Config, bot: Bot,
) -> None:
    """Будь-яке звичайне текстове повідомлення (не команда, не в FSM) обробляється AI-агентом."""
    if not message.from_user or not message.text:
        return
    if await state.get_state() is not None:
        return

    user = await get_or_create_user(
        session, message.from_user.id,
        message.from_user.username, message.from_user.full_name,
    )
    language = user.language or "ua"

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

    await _send_ai_answer(message, response_text, model_used, language)
