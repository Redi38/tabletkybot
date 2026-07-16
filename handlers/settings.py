import logging
from html import escape as html_escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from config import Config
from database import crud
from locales.texts import btn_variants, get_text
from services.geo_service import format_timezone_display, resolve_timezone_from_place
from services.scheduler import add_reminders_for_medicine

router = Router()
logger = logging.getLogger(__name__)


class SettingsState(StatesGroup):
    waiting_name = State()
    waiting_timezone = State()
    waiting_feedback = State()
    lang = State()


def settings_keyboard(language: str = "ua") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(language, "btn_change_name"), callback_data="set_name", style="primary")],
        [InlineKeyboardButton(text=get_text(language, "btn_change_tz"), callback_data="set_tz", style="primary")],
        [InlineKeyboardButton(text=get_text(language, "btn_lang"), callback_data="set_lang", style="primary")],
        [InlineKeyboardButton(text=get_text(language, "btn_feedback"), callback_data="set_feedback", style="primary")],
    ])


async def _settings_ctx(
        call: CallbackQuery, state: FSMContext, session: AsyncSession
) -> tuple[Message, str] | None:
    if not isinstance(call.message, Message) or not call.from_user:
        return None
    lang = await crud.get_user_language(session, call.from_user.id)
    await state.update_data(lang=lang)
    return call.message, lang


async def _msg_ctx(message: Message, state: FSMContext) -> tuple[str, str] | None:
    if not message.text or not message.from_user:
        return None
    data = await state.get_data()
    return message.text.strip(), data.get("lang", "ua")


@router.message(F.text.in_(btn_variants("btn_settings")))
async def settings_menu(message: Message, session: AsyncSession) -> None:
    if not message.from_user:
        return
    user = await crud.get_or_create_user(
        session, message.from_user.id,
        message.from_user.username, message.from_user.full_name,
    )
    lang = user.language or "ua"
    tz_display = format_timezone_display(user.timezone) or get_text(lang, "not_set")
    await message.answer(
        get_text(lang, "settings_title", name=str(user.full_name), tz=tz_display),
        reply_markup=settings_keyboard(lang),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "set_name")
async def edit_name_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _settings_ctx(call, state, session)
    if not ctx:
        return
    msg, lang = ctx
    await msg.edit_text(get_text(lang, "ask_new_name"), parse_mode="HTML")
    await state.set_state(SettingsState.waiting_name)


@router.message(SettingsState.waiting_name)
async def edit_name_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _msg_ctx(message, state)
    if not ctx or not message.from_user:
        return
    new_name, lang = ctx
    user = await crud.get_or_create_user(
        session, message.from_user.id,
        message.from_user.username, message.from_user.full_name,
    )
    user.full_name = new_name
    await session.flush()
    await state.clear()
    await message.answer(get_text(lang, "name_updated"))


@router.callback_query(F.data == "set_tz")
async def edit_tz_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _settings_ctx(call, state, session)
    if not ctx:
        return
    msg, lang = ctx
    await msg.edit_text(get_text(lang, "ask_new_tz"), parse_mode="HTML")
    await state.set_state(SettingsState.waiting_timezone)


@router.message(SettingsState.waiting_timezone)
async def edit_tz_save(message: Message, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    ctx = await _msg_ctx(message, state)
    if not ctx or not message.from_user:
        return
    place_text, lang = ctx

    tz_name = await resolve_timezone_from_place(place_text)

    if not tz_name:
        await message.answer(get_text(lang, "err_timezone_place"), parse_mode="HTML")
        return

    await crud.update_user_timezone(session, message.from_user.id, tz_name)
    medicines = await crud.get_user_medicines(session, message.from_user.id, active_only=True)
    for med in medicines:
        add_reminders_for_medicine(bot=bot, medicine=med, timezone=tz_name,
                                   chat_id=message.from_user.id, language=lang)
    await state.clear()
    await message.answer(get_text(lang, "tz_updated_with_name", tz=format_timezone_display(tz_name)), parse_mode="HTML")


# ── Language ────────────────────────────────────────────────
@router.callback_query(F.data == "set_lang")
async def edit_lang_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _settings_ctx(call, state, session)
    if not ctx:
        return
    msg, lang = ctx
    await msg.edit_text(get_text(lang, "lang_choose"), reply_markup=language_keyboard())


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Українська", callback_data="lang_ua", style="primary"),
        InlineKeyboardButton(text="English", callback_data="lang_en", style="primary"),
        InlineKeyboardButton(text="Русский", callback_data="lang_ru", style="primary"),
    ]])


# ── Feedback ──────────────────────────────────────────────────
@router.callback_query(F.data == "set_feedback")
async def feedback_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _settings_ctx(call, state, session)
    if not ctx:
        return
    msg, lang = ctx
    await msg.edit_text(get_text(lang, "ask_feedback"), parse_mode="HTML")
    await state.set_state(SettingsState.waiting_feedback)


@router.message(SettingsState.waiting_feedback)
async def feedback_save(message: Message, state: FSMContext, bot: Bot, config: Config) -> None:
    ctx = await _msg_ctx(message, state)
    if not ctx or not message.from_user:
        return
    feedback_text, lang = ctx
    await state.clear()

    if not config.admin_chat_id:
        logger.warning(
            f"Feedback from user {message.from_user.id} (@{message.from_user.username}) could not be "
            f"forwarded — ADMIN_CHAT_ID is not configured: {feedback_text}"
        )
        await message.answer(get_text(lang, "err_feedback_unavailable"), parse_mode="HTML")
        return

    forward_text = get_text(
        lang, "feedback_admin_header",
        name=html_escape(message.from_user.full_name),
        username=html_escape(message.from_user.username or "—"),
        user_id=message.from_user.id,
        text=html_escape(feedback_text),
    )

    try:
        await bot.send_message(chat_id=config.admin_chat_id, text=forward_text, parse_mode="HTML")
        logger.info(f"Feedback forwarded from user {message.from_user.id} (@{message.from_user.username})")
        await message.answer(get_text(lang, "feedback_sent"), parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to forward feedback from user {message.from_user.id}: {e}")
        await message.answer(get_text(lang, "err_feedback_unavailable"), parse_mode="HTML")
