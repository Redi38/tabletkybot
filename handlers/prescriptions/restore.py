"""Handlers for restoring an archived prescription with new dates/quantity."""

import logging
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from locales.texts import get_text

from .keyboards import duration_kb, prescription_menu_kb
from .states import RestorePrescription
from .utils import _valid_prescription_ctx, parse_date, parse_optional_int

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("presc_restore_"))
async def restore_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, _ = ctx
    await state.update_data(lang=lang, prescription_id=prescription_id)
    await msg.edit_text(get_text(lang, "add_presc_valid_from"), parse_mode="HTML")
    await state.set_state(RestorePrescription.valid_from)
    await call.answer()


@router.message(RestorePrescription.valid_from)
async def restore_valid_from(message: Message, state: FSMContext) -> None:
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
    await state.set_state(RestorePrescription.duration)


@router.callback_query(RestorePrescription.duration, F.data.in_({"presc_dur_30", "presc_dur_60"}))
async def restore_duration(call: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(call.message, Message) or not call.data:
        return
    data = await state.get_data()
    lang = data.get("lang", "ua")
    days = int(str(call.data).split("_")[-1])
    valid_from = date.fromisoformat(data["valid_from"])
    expires_at = valid_from + timedelta(days=days)
    await state.update_data(expires=expires_at.isoformat())
    await call.message.edit_text(get_text(lang, "add_presc_quantity"), parse_mode="HTML")
    await state.set_state(RestorePrescription.quantity)
    await call.answer()


@router.message(RestorePrescription.quantity)
async def restore_quantity(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        return
    data = await state.get_data()
    lang = data.get("lang", "ua")
    qty = parse_optional_int(message.text)
    if qty == -1:
        await message.answer(get_text(lang, "err_stock"), parse_mode="HTML")
        return

    valid_from = date.fromisoformat(data["valid_from"])
    await crud.restore_prescription(
        session,
        data["prescription_id"],
        valid_from=valid_from,
        expires_at=date.fromisoformat(data["expires"]),
        max_quantity=qty,
    )

    if message.from_user:
        logger.info(
            f"User {message.from_user.id} (@{message.from_user.username}) restored prescription (id={data['prescription_id']}) from archive"
        )

    await state.clear()
    await message.answer(get_text(lang, "presc_restored"), reply_markup=prescription_menu_kb(lang), parse_mode="HTML")
