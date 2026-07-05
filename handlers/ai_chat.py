import logging
import aiohttp
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from services.ai_service import get_ai_response, get_ai_vision_response, get_ai_agent_response
from config import Config
from locales.texts import get_text, TEXTS, btn_variants

router = Router()
logger = logging.getLogger(__name__)


class AIState(StatesGroup):
    active = State()


def ai_keyboard(language: str = "ua") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=get_text(language, "ai_btn_clear"))],
            [KeyboardButton(text=get_text(language, "ai_btn_exit"))],
        ],
        resize_keyboard=True,
        input_field_placeholder=get_text(language, "ai_placeholder"),
    )


async def user_main_keyboard(session: AsyncSession, user_id: int) -> ReplyKeyboardMarkup:
    from handlers.start import get_main_keyboard
    language = await crud.get_user_language(session, user_id)
    return get_main_keyboard(language)


@router.message(F.text.in_(btn_variants("btn_ai")))
@router.message(Command("ai"))
async def enter_ai_mode(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.from_user:
        return

    await state.set_state(AIState.active)
    language = await crud.get_user_language(session, message.from_user.id)
    await message.answer(
        get_text(language, "ai_enabled"),
        parse_mode="HTML",
        reply_markup=ai_keyboard(language)
    )


@router.message(AIState.active, F.text.in_(btn_variants("ai_btn_exit")))
async def exit_ai_mode(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.from_user:
        return

    await state.clear()
    language = await crud.get_user_language(session, message.from_user.id)
    await message.answer(
        get_text(language, "ai_exited"),
        reply_markup=await user_main_keyboard(session, message.from_user.id)
    )


@router.message(AIState.active, F.text.in_(btn_variants("ai_btn_clear")))
async def clear_context(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return

    await crud.clear_chat_history(session, message.from_user.id)
    language = await crud.get_user_language(session, message.from_user.id)
    await message.answer(
        get_text(language, "ai_cleared"),
        reply_markup=ai_keyboard(language)
    )


async def download_telegram_file(bot: Bot, file_id: str) -> bytes:
    """Завантажити файл з Telegram."""
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


# ─── Логіка збереження та відправки ──────────────────────────────────────
async def process_and_send_ai_response(
    message: Message,
    session: AsyncSession,
    user_id: int,
    user_text: str,
    response_text: str,
    model_used: str,
    language: str
) -> None:
    """Загальна функція для збереження історії та відправки відповіді AI."""
    # Зберігаємо повідомлення в БД
    await crud.add_chat_message(session, user_id, "user", user_text)
    await crud.add_chat_message(session, user_id, "assistant", response_text)

    formatted_response = f"{response_text}\n\n<i>🔮 {get_text(language, 'ai_model')}: {model_used}</i>"

    try:
        await message.answer(formatted_response, parse_mode="HTML", reply_markup=ai_keyboard(language))
    except TelegramBadRequest as e:
        if "can't parse entities" in str(e).lower():
            logger.warning(f"Згенеровано невалідний HTML. Відправляємо сирий текст. Помилка: {e}")
            await message.answer(formatted_response, parse_mode=None, reply_markup=ai_keyboard(language))
        else:
            raise
# ────────────────────────────────────────────────────────────────────────


@router.message(AIState.active, F.photo)
async def handle_photo(message: Message, session: AsyncSession, config: Config, bot: Bot) -> None:
    """Обробка фото через Vision модель."""
    if not message.from_user or not message.photo:
        return

    await bot.send_chat_action(message.chat.id, "typing")
    language = await crud.get_user_language(session, message.from_user.id)

    await crud.get_or_create_user(
        session=session,
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )

    photo = message.photo[-1]
    caption = message.caption or get_text(language, "ai_analyze_photo")

    await message.answer(
        get_text(language, "ai_analyzing"),
        reply_markup=ai_keyboard(language)
    )

    try:
        image_bytes = await download_telegram_file(bot, photo.file_id)
        response_text, model_used = await get_ai_vision_response(config, image_bytes, caption, language)

        await process_and_send_ai_response(
            message, session, message.from_user.id, f"[Фото] {caption}", response_text, model_used, language
        )

    except Exception as e:
        logger.error(f"Помилка обробки фото AI: {e}")
        await message.answer(
            get_text(language, "ai_err_photo"),
            reply_markup=ai_keyboard(language)
        )


@router.message(AIState.active, F.document)
async def handle_document(message: Message, session: AsyncSession, config: Config, bot: Bot) -> None:
    """Обробка PDF документів."""
    doc = message.document
    if not message.from_user or not doc:
        return

    language = await crud.get_user_language(session, message.from_user.id)

    if doc.mime_type == "application/pdf":
        await bot.send_chat_action(message.chat.id, "typing")
        await message.answer(
            get_text(language, "ai_process_pdf"),
            reply_markup=ai_keyboard(language)
        )

        try:
            pdf_bytes = await download_telegram_file(bot, doc.file_id)
            image_bytes = await pdf_to_image(pdf_bytes)

            if image_bytes is None:
                await message.answer(
                    get_text(language, "ai_err_pdf_lib"),
                    parse_mode="HTML",
                    reply_markup=ai_keyboard(language)
                )
                return

            caption = message.caption or get_text(language, "ai_analyze_pdf")
            response_text, model_used = await get_ai_vision_response(config, image_bytes, caption, language)

            await crud.get_or_create_user(
                session=session,
                user_id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name,
            )

            await process_and_send_ai_response(
                message, session, message.from_user.id, f"[PDF] {caption}", response_text, model_used, language
            )

        except Exception as e:
            logger.error(f"Помилка обробки PDF документа: {e}")
            await message.answer(
                get_text(language, "ai_err_pdf"),
                reply_markup=ai_keyboard(language)
            )
    else:
        await message.answer(
            get_text(language, "ai_err_type", mime=str(doc.mime_type)),
            parse_mode="HTML",
            reply_markup=ai_keyboard(language)
        )


@router.message(AIState.active)
async def handle_ai_message(message: Message, session: AsyncSession, config: Config, state: FSMContext,
                            bot: Bot) -> None:
    """Обробка текстових повідомлень."""
    if not message.from_user:
        return

    user_id = message.from_user.id
    language = await crud.get_user_language(session, user_id)

    if not message.text:
        await message.answer(
            get_text(language, "ai_empty_input"),
            reply_markup=ai_keyboard(language)
        )
        return

    main_buttons = (
    btn_variants("btn_medicines") | btn_variants("btn_report") |
    btn_variants("btn_ai") | btn_variants("btn_prescriptions") | btn_variants("btn_lang")
    )

    if message.text in main_buttons:
        await state.clear()
        await message.answer(
            get_text(language, "ai_exited"),
            reply_markup=await user_main_keyboard(session, user_id)
        )
        return

    await bot.send_chat_action(message.chat.id, "typing")

    await crud.get_or_create_user(
        session=session,
        user_id=user_id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )

    history = await crud.get_chat_history(session, user_id, limit=10)

    if history and not isinstance(history[0], dict):
        messages = [{"role": msg.role, "content": msg.content} for msg in history]
    else:
        messages = list(history)

    messages.append({"role": "user", "content": message.text})

    response_text, model_used = await get_ai_agent_response(config, session, user_id, messages, language=language)

    await process_and_send_ai_response(
        message, session, user_id, message.text, response_text, model_used, language
    )
