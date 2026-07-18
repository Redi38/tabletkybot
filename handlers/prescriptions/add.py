"""Handlers for the "add a new prescription" FSM flow."""

import logging
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from locales.texts import get_text

from .keyboards import duration_kb, prescription_menu_kb
from .states import AddPrescription
from .utils import _base_ctx, parse_date, parse_optional_int

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "presc_add")
async def add_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx:
        return
    msg, lang = ctx
    await state.update_data(lang=lang)
    await msg.edit_text(get_text(lang, "add_presc_name"), parse_mode="HTML")
    await state.set_state(AddPrescription.name)


@router.message(AddPrescription.name)
async def add_name(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "ua")
    await state.update_data(name=message.text.strip())
    await message.answer(get_text(lang, "add_presc_valid_from"), parse_mode="HTML")
    await state.set_state(AddPrescription.valid_from)


@router.message(AddPrescription.valid_from)
async def add_valid_from(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "ua")
    valid_from = parse_date(message.text)
    if not valid_from:
        await message.answer(get_text(lang, "err_date"), parse_mode="HTML")
        return
    await state.update_data(valid_from=valid_from.isoformat())
    await message.answer(
        get_text(lang, "presc_choose_duration"),
        reply_markup=duration_kb(lang),
        parse_mode="HTML",
    )
    await state.set_state(AddPrescription.duration)


@router.callback_query(AddPrescription.duration, F.data.in_({"presc_dur_30", "presc_dur_60"}))
async def duration_chosen(call: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(call.message, Message) or not call.data:
        return
    data = await state.get_data()
    lang = data.get("lang", "ua")
    days = int(str(call.data).split("_")[-1])

    valid_from = date.fromisoformat(data["valid_from"])
    expires_at = valid_from + timedelta(days=days)
    await state.update_data(expires=expires_at.isoformat())

    await call.message.edit_text(get_text(lang, "add_presc_quantity"), parse_mode="HTML")
    await state.set_state(AddPrescription.quantity)
    await call.answer()


@router.message(AddPrescription.quantity)
async def add_quantity(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    lang = (await state.get_data()).get("lang", "ua")
    qty = parse_optional_int(message.text)
    if qty == -1:
        await message.answer(get_text(lang, "err_stock"), parse_mode="HTML")
        return
    await state.update_data(quantity=qty)
    await message.answer(get_text(lang, "add_presc_reminder"), parse_mode="HTML")
    await state.set_state(AddPrescription.reminder)


@router.message(AddPrescription.reminder)
async def add_reminder(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text or not message.from_user:
        return
    data = await state.get_data()
    lang = data.get("lang", "ua")
    text = message.text.strip()

    reminder_days = 3
    if text != "-":
        val = parse_optional_int(text)
        if val == -1:
            await message.answer(get_text(lang, "err_invalid_number"), parse_mode="HTML")
            return
        if val is not None:
            reminder_days = val

    valid_from = date.fromisoformat(data["valid_from"])
    prescription = await crud.add_prescription(
        session=session,
        user_id=message.from_user.id,
        medicine_name=data["name"],
        valid_from=valid_from,
        expires_at=date.fromisoformat(data["expires"]),
        max_quantity=data.get("quantity"),
        reminder_days_before=reminder_days,
    )
    await state.clear()
    logger.info(
        f"User {message.from_user.id} (@{message.from_user.username}) added prescription "
        f"'{prescription.medicine_name}' (id={prescription.id}), valid until {prescription.expires_at}"
    )
    await message.answer(
        get_text(
            lang,
            "presc_added",
            name=prescription.medicine_name,
            valid_from=prescription.valid_from.strftime("%d.%m.%Y"),
            expires=prescription.expires_at.strftime("%d.%m.%Y"),
        ),
        parse_mode="HTML",
        reply_markup=prescription_menu_kb(lang),
    )
