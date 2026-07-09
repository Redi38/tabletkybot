import logging
import aiohttp
from config import Config
from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession
from database.crud import get_or_create_user, get_user_language
from database import crud
from locales.texts import get_text
from services.ai_service import get_ai_agent_response, get_ai_vision_response, format_markdown_to_html, strip_html_tags
from services.scheduler import remove_reminders
from handlers.start import get_main_keyboard

router = Router()
logger = logging.getLogger(__name__)


def build_removal_confirm_kb(confirmation: dict, language: str = "ua") -> InlineKeyboardMarkup:
    """Кнопки Архівувати / Видалити / Назад для AI-агента."""
    target_type = confirmation["target_type"]  # "medicine" або "prescription"
    target_id = confirmation["target_id"]
    prefix = "med" if target_type == "medicine" else "presc"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=get_text(language, "btn_ai_archive"), callback_data=f"ai_act_{prefix}_archive_{target_id}"),
            InlineKeyboardButton(text=get_text(language, "btn_ai_delete"), callback_data=f"ai_act_{prefix}_delete_{target_id}"),
        ],
        [InlineKeyboardButton(text=get_text(language, "btn_back"), callback_data="ai_act_cancel")],
    ])


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
    language = user.language or "ua"

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
        conv_messages = [{"role": m.role, "content": strip_html_tags(m.content)} for m in history]
    else:
        conv_messages = [{"role": m["role"], "content": strip_html_tags(m["content"])} for m in history]
    conv_messages.append({"role": "user", "content": message.text})

    raw_text, model_used, confirmation = await get_ai_agent_response(
        config, session, message.from_user.id, conv_messages, language=language,
    )

    if confirmation:
        await crud.add_chat_message(session, message.from_user.id, "user", message.text)
        await crud.add_chat_message(
            session, message.from_user.id, "assistant",
            f"[Запитано підтвердження видалення: {confirmation['target_name']}]",
        )
        text = get_text(language, "ai_confirm_removal_prompt", name=confirmation["target_name"])
        await message.answer(text, reply_markup=build_removal_confirm_kb(confirmation, language))
        return

    await crud.add_chat_message(session, message.from_user.id, "user", message.text)
    await crud.add_chat_message(session, message.from_user.id, "assistant", raw_text)

    formatted_text = format_markdown_to_html(raw_text)
    await _send_ai_answer(message, formatted_text, model_used, language)


@router.callback_query(F.data.startswith("ai_act_"))
async def handle_ai_action_confirm(call: CallbackQuery, session: AsyncSession) -> None:
    """Обробка кнопок Архівувати/Видалити/Назад після запиту від AI-агента."""
    if not call.data or not isinstance(call.message, Message) or not call.from_user:
        return

    language = await get_user_language(session, call.from_user.id)

    if call.data == "ai_act_cancel":
        await call.message.edit_text(get_text(language, "ai_action_cancelled"))
        await call.answer()
        return

    parts = call.data.split("_")  # ["ai", "act", prefix, action, id]
    if len(parts) != 5:
        return
    _, _, prefix, action, target_id_str = parts
    target_id = int(target_id_str)

    if prefix == "med":
        medicine = await crud.get_medicine_by_id(session, target_id)
        if not medicine or medicine.user_id != call.from_user.id:
            await call.answer(get_text(language, "ai_target_not_found"), show_alert=True)
            return
        name = medicine.name
        if action == "archive":
            await crud.update_medicine_field(session, target_id, "is_active", False)
            remove_reminders(target_id)
            await call.message.edit_text(get_text(language, "ai_medicine_archived", name=name))
        else:
            await crud.delete_medicine(session, target_id)
            remove_reminders(target_id)
            await call.message.edit_text(get_text(language, "ai_medicine_deleted", name=name))

    else:  # prefix == "presc"
        prescription = await crud.get_prescription_by_id(session, target_id)
        if not prescription or prescription.user_id != call.from_user.id:
            await call.answer(get_text(language, "ai_target_not_found"), show_alert=True)
            return
        name = prescription.medicine_name
        if action == "archive":
            await crud.archive_prescription(session, target_id)
            await call.message.edit_text(get_text(language, "ai_prescription_archived", name=name))
        else:
            await crud.delete_prescription(session, target_id)
            await call.message.edit_text(get_text(language, "ai_prescription_deleted", name=name))

    await call.answer()
