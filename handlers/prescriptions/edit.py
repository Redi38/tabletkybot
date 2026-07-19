"""Handlers for editing an existing prescription's fields."""

import logging
from datetime import timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from locales.texts import data_lang, get_text

from .keyboards import edit_duration_kb, edit_field_kb, prescription_menu_kb
from .states import EditPrescription
from .utils import _base_ctx, _valid_prescription_ctx, parse_date, parse_optional_int

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("presc_edit_"))
async def edit_menu(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, prescription = ctx
    await msg.edit_text(
        get_text(lang, "presc_edit_title", name=prescription.medicine_name),
        reply_markup=edit_field_kb(prescription_id, lang),
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data.startswith("presc_ef_valid_"))
async def edit_valid_from_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, _ = ctx
    await state.update_data(lang=lang, prescription_id=prescription_id)
    await msg.edit_text(get_text(lang, "add_presc_valid_from"), parse_mode="HTML")
    await state.set_state(EditPrescription.valid_from)
    await call.answer()


@router.message(EditPrescription.valid_from)
async def edit_valid_from_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        return
    data = await state.get_data()
    lang = data_lang(data)
    new_date = parse_date(message.text)
    if not new_date:
        await message.answer(get_text(lang, "err_date"), parse_mode="HTML")
        return

    prescription_id = data["prescription_id"]
    prescription = await crud.get_prescription_by_id(session, prescription_id)
    if not prescription:
        await state.clear()
        return

    # ── Keep the previous duration and shift the expiration date ────
    duration_days = (prescription.expires_at - prescription.valid_from).days
    new_expires = new_date + timedelta(days=duration_days)

    await crud.update_prescription_field(session, prescription_id, "valid_from", new_date)
    await crud.update_prescription_field(session, prescription_id, "expires_at", new_expires)

    if message.from_user:
        logger.info(
            f"User {message.from_user.id} (@{message.from_user.username}) edited valid_from for "
            f"prescription '{prescription.medicine_name}' (id={prescription_id}) to {new_date}, "
            f"new expires_at={new_expires}"
        )

    await state.clear()
    await message.answer(get_text(lang, "presc_updated"), reply_markup=prescription_menu_kb(lang), parse_mode="HTML")


@router.callback_query(F.data.startswith("presc_ef_duration_"))
async def edit_duration_start(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, _ = ctx
    await msg.edit_text(
        get_text(lang, "presc_choose_duration"),
        reply_markup=edit_duration_kb(prescription_id, lang),
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data.startswith("presc_edur_"))
async def edit_duration_save(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.data:
        return
    msg, lang = ctx
    parts = str(call.data).split("_")
    days, prescription_id = int(parts[2]), int(parts[3])

    prescription = await crud.get_prescription_by_id(session, prescription_id)
    if not prescription:
        return
    new_expires = prescription.valid_from + timedelta(days=days)
    await crud.update_prescription_field(session, prescription_id, "expires_at", new_expires)

    if call.from_user:
        logger.info(
            f"User {call.from_user.id} (@{call.from_user.username}) changed duration to {days} days for "
            f"prescription '{prescription.medicine_name}' (id={prescription_id}), new expires_at={new_expires}"
        )

    await msg.edit_text(get_text(lang, "presc_updated"), reply_markup=prescription_menu_kb(lang), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("presc_ef_quantity_"))
async def edit_quantity_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, _ = ctx
    await state.update_data(lang=lang, prescription_id=prescription_id)
    await msg.edit_text(get_text(lang, "add_presc_quantity"), parse_mode="HTML")
    await state.set_state(EditPrescription.quantity)
    await call.answer()


@router.message(EditPrescription.quantity)
async def edit_quantity_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        return
    data = await state.get_data()
    lang = data_lang(data)
    qty = parse_optional_int(message.text)
    if qty == -1:
        await message.answer(get_text(lang, "err_stock"), parse_mode="HTML")
        return
    await crud.update_prescription_field(session, data["prescription_id"], "max_quantity", qty)

    if message.from_user:
        logger.info(
            f"User {message.from_user.id} (@{message.from_user.username}) changed max_quantity to {qty} "
            f"for prescription (id={data['prescription_id']})"
        )

    await state.clear()
    await message.answer(get_text(lang, "presc_updated"), reply_markup=prescription_menu_kb(lang), parse_mode="HTML")
