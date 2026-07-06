import pytz
from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from database import crud
from locales.texts import get_text, TEXTS, btn_variants
from services.scheduler import add_reminders_for_medicine

router = Router()


class SettingsState(StatesGroup):
    waiting_name = State()
    waiting_timezone = State()
    lang = State()


def settings_keyboard(language: str = "ua") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(language, "btn_change_name"), callback_data="set_name", style="primary")],
        [InlineKeyboardButton(text=get_text(language, "btn_change_tz"), callback_data="set_tz", style="primary")],
        [InlineKeyboardButton(text=get_text(language, "btn_lang"), callback_data="set_lang", style="primary")],
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
    await message.answer(
        get_text(lang, "settings_title", name=str(user.full_name), tz=str(user.timezone)),
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
    new_tz, lang = ctx

    try:
        pytz.timezone(new_tz)
    except pytz.UnknownTimeZoneError:
        await message.answer(get_text(lang, "err_timezone"), parse_mode="HTML")
        return

    await crud.update_user_timezone(session, message.from_user.id, new_tz)
    medicines = await crud.get_user_medicines(session, message.from_user.id, active_only=True)
    for med in medicines:
        add_reminders_for_medicine(bot=bot, medicine=med, timezone=new_tz,
                                   chat_id=message.from_user.id, language=lang)
    await state.clear()
    await message.answer(get_text(lang, "tz_updated"), parse_mode="HTML")


# ── Мова ────────────────────────────────────────────────────
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
