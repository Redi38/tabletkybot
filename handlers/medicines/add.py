"""Handlers for the "add a new medicine" FSM flow."""

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database import crud
from locales.texts import get_text
from services.geo_service import format_timezone_display, resolve_timezone_from_place
from services.scheduler import add_reminders_for_medicine

from .keyboards import medicine_menu_kb, track_stock_kb
from .states import AddMedicine
from .utils import _base_ctx, parse_int, parse_times

router = Router()
logger = logging.getLogger(__name__)


# ── Adding a medicine ──────────────────────────────────────────────────────
@router.callback_query(F.data == "med_add")
async def add_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx:
        return
    msg, lang = ctx
    await state.update_data(lang=lang)
    await msg.edit_text(get_text(lang, "add_name"), parse_mode="HTML")
    await state.set_state(AddMedicine.name)


@router.message(AddMedicine.name)
async def add_name(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "ua")
    await state.update_data(name=message.text.strip())
    await message.answer(get_text(lang, "add_form"), parse_mode="HTML")
    await state.set_state(AddMedicine.form)


@router.message(AddMedicine.form)
async def add_form(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "ua")
    await state.update_data(form=message.text.strip())
    await message.answer(get_text(lang, "add_dosage"), parse_mode="HTML")
    await state.set_state(AddMedicine.dosage)


@router.message(AddMedicine.dosage)
async def add_dosage(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "ua")
    await state.update_data(dosage=message.text.strip())
    await message.answer(get_text(lang, "add_time"), parse_mode="HTML")
    await state.set_state(AddMedicine.time)


@router.message(AddMedicine.time)
async def add_time(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "ua")
    parsed_times = parse_times(message.text)
    if not parsed_times:
        await message.answer(get_text(lang, "err_time"), parse_mode="HTML")
        return
    await state.update_data(time=parsed_times)
    await message.answer(get_text(lang, "add_duration"), parse_mode="HTML")
    await state.set_state(AddMedicine.duration)


@router.message(AddMedicine.duration)
async def add_duration(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text or not message.from_user:
        return
    lang = (await state.get_data()).get("lang", "ua")
    days = parse_int(message.text.strip())
    if days is None or days == 0:
        await message.answer(get_text(lang, "err_duration"))
        return
    await state.update_data(duration=days)
    user = await crud.get_or_create_user(
        session, message.from_user.id, message.from_user.username, message.from_user.full_name
    )
    if user.timezone:
        await state.update_data(timezone=user.timezone)
        await message.answer(get_text(lang, "ask_track_stock"), reply_markup=track_stock_kb(lang), parse_mode="HTML")
        await state.set_state(AddMedicine.track_stock)
    else:
        await message.answer(get_text(lang, "add_timezone"), parse_mode="HTML")
        await state.set_state(AddMedicine.timezone)


@router.message(AddMedicine.timezone)
async def add_timezone(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text or not message.from_user:
        return
    lang = (await state.get_data()).get("lang", "ua")
    place_text = message.text.strip()

    tz_name = await resolve_timezone_from_place(place_text)

    if not tz_name:
        await message.answer(get_text(lang, "err_timezone"), parse_mode="HTML")
        return

    await state.update_data(timezone=tz_name)
    await crud.update_user_timezone(session, message.from_user.id, tz_name)
    await message.answer(get_text(lang, "timezone_resolved", tz=format_timezone_display(tz_name)), parse_mode="HTML")
    await message.answer(get_text(lang, "ask_track_stock"), reply_markup=track_stock_kb(lang), parse_mode="HTML")
    await state.set_state(AddMedicine.track_stock)


@router.callback_query(F.data.startswith("track_stock_"))
async def add_track_stock(
    call: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    session_factory: async_sessionmaker,
) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.data:
        return
    msg, lang = ctx
    if str(call.data).split("_")[2] == "yes":
        await msg.edit_text(get_text(lang, "add_stock_amount"), parse_mode="HTML")
        await state.set_state(AddMedicine.stock_amount)
    else:
        await _save_new_medicine(msg, state, session, bot, lang, None, None, session_factory)


@router.message(AddMedicine.stock_amount)
async def add_stock_amount(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "ua")
    amount = parse_int(message.text.strip())
    if amount is None:
        await message.answer(get_text(lang, "err_stock"))
        return
    await state.update_data(stock_amount=amount)
    await message.answer(get_text(lang, "ask_stock_threshold"), parse_mode="HTML")
    await state.set_state(AddMedicine.stock_threshold)


@router.message(AddMedicine.stock_threshold)
async def add_stock_threshold(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    session_factory: async_sessionmaker,
) -> None:
    if not message.text:
        return
    data = await state.get_data()
    lang = data.get("lang", "ua")
    threshold = parse_int(message.text.strip())
    if threshold is None:
        await message.answer(get_text(lang, "err_invalid_number"))
        return
    await _save_new_medicine(message, state, session, bot, lang, data["stock_amount"], threshold, session_factory)


async def _save_new_medicine(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    bot: Bot,
    lang: str,
    stock_amount: int | None,
    threshold: int | None,
    session_factory: async_sessionmaker,
) -> None:
    data = await state.get_data()
    user_id = message.chat.id
    total_doses = data["duration"] * len(data["time"])

    medicine = await crud.add_medicine(
        session=session,
        user_id=user_id,
        name=data["name"],
        form=data["form"],
        dosage=data["dosage"],
        schedules_list=data["time"],
        course_duration=total_doses,
        stock_amount=stock_amount,
        low_stock_threshold=threshold,
    )
    if not medicine:
        return

    add_reminders_for_medicine(bot, medicine, data["timezone"], user_id, lang, session_factory=session_factory)
    await state.clear()
    times_str = ", ".join(data["time"])
    username = message.from_user.username if message.from_user else None
    logger.info(
        f"User {user_id} (@{username}) added medicine '{medicine.name}' (id={medicine.id}), schedule={times_str}"
    )
    await message.answer(
        get_text(lang, "med_added", name=str(medicine.name), time=times_str, duration=str(data["duration"])),
        parse_mode="HTML",
        reply_markup=medicine_menu_kb(lang),
    )
