"""Handlers for adding a purchased quantity to a medicine's stock, and the
finish-archive/keep-active follow-up prompts after a full purchase."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from locales.texts import get_text

from .states import AddPurchaseToStock
from .utils import _base_ctx, _valid_prescription_ctx, parse_positive_int

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "presc_stock_no")
async def stock_add_declined(call: CallbackQuery) -> None:
    if isinstance(call.message, Message):
        try:
            await call.message.delete()
        except Exception:
            pass
    await call.answer()


@router.callback_query(F.data.startswith("presc_stock_yes_"))
async def stock_add_start(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.data:
        return
    msg, lang = ctx
    # presc_stock_yes_{prescription_id}_{amount}
    parts = str(call.data).split("_")
    amount = int(parts[-1])

    await state.update_data(lang=lang, purchased_amount=amount)
    await msg.edit_text(get_text(lang, "ask_pack_size"), parse_mode="HTML")
    await state.set_state(AddPurchaseToStock.waiting_pack_size)
    await call.answer()


@router.message(AddPurchaseToStock.waiting_pack_size)
async def stock_pack_size_entered(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text or not message.from_user:
        return
    data = await state.get_data()
    lang = data.get("lang", "ua")

    pack_size = parse_positive_int(message.text)
    if pack_size is None:
        await message.answer(get_text(lang, "err_stock"), parse_mode="HTML")
        return

    amount = data["purchased_amount"]
    total = amount * pack_size

    medicines = await crud.get_user_medicines(session, message.from_user.id, active_only=True)
    if not medicines:
        await message.answer(get_text(lang, "presc_stock_no_medicines"), parse_mode="HTML")
        await state.clear()
        return

    await state.update_data(total=total)
    buttons = [[InlineKeyboardButton(text=f"💊 {m.name}", callback_data=f"presc_stock_pick_{m.id}")] for m in medicines]
    await message.answer(
        get_text(lang, "presc_stock_choose_medicine", total=total),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )
    await state.set_state(AddPurchaseToStock.waiting_medicine_choice)


@router.callback_query(AddPurchaseToStock.waiting_medicine_choice, F.data.startswith("presc_stock_pick_"))
async def stock_medicine_picked(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    if not isinstance(call.message, Message) or not call.data:
        return
    data = await state.get_data()
    lang = data.get("lang", "ua")
    total = data["total"]
    medicine_id = int(str(call.data).split("_")[-1])

    new_stock = await crud.add_stock(session, medicine_id, total)
    medicine = await crud.get_medicine_by_id(session, medicine_id)
    name = medicine.name if medicine else "?"

    if call.from_user:
        logger.info(
            f"User {call.from_user.id} (@{call.from_user.username}) added prescription purchase "
            f"({total} units) to medicine '{name}' (id={medicine_id}) stock, new stock={new_stock}"
        )

    await call.message.edit_text(
        get_text(lang, "presc_stock_added", total=total, name=name, stock=new_stock),
        parse_mode="HTML",
    )
    await state.clear()
    await call.answer()


@router.callback_query(F.data.startswith("presc_finish_archive_"))
async def finish_archive(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, prescription = ctx
    await crud.archive_prescription(session, prescription_id)
    logger.info(
        f"User {call.from_user.id} (@{call.from_user.username}) archived prescription '{prescription.medicine_name}' (id={prescription_id}) after full purchase"
    )
    await msg.edit_text(get_text(lang, "presc_archived", name=prescription.medicine_name), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("presc_finish_keep_"))
async def finish_keep(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx:
        return
    msg, lang = ctx
    if call.from_user:
        logger.info(f"User {call.from_user.id} (@{call.from_user.username}) kept a fully-purchased prescription active")
    await msg.edit_text(get_text(lang, "presc_kept_active"), parse_mode="HTML")
    await call.answer()
