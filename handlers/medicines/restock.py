"""Handlers for restocking a medicine after it runs low or empty."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from locales.texts import get_text
from services.scheduler import clear_stock_alert_pending

from .states import RestockMedicine
from .utils import _valid_medicine_ctx, parse_int

router = Router()
logger = logging.getLogger(__name__)


# ── Restocking ────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("restock_yes_") | F.data.startswith("restock_ask_"))
async def restock_ask_amount(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _valid_medicine_ctx(call, session)
    if not ctx or not call.data:
        return
    msg, lang, medicine_id, _ = ctx
    await clear_stock_alert_pending(call.from_user.id, medicine_id)
    needs_take = "restock_yes" in str(call.data)
    await state.update_data(medicine_id=medicine_id, lang=lang, needs_take=needs_take)
    await msg.edit_text(get_text(lang, "ask_restock_amount"), parse_mode="HTML")
    await state.set_state(RestockMedicine.waiting_for_amount)


@router.callback_query(F.data.startswith("restock_no_"))
async def restock_skip(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _valid_medicine_ctx(call, session)
    if not ctx:
        return
    msg, lang, medicine_id, _ = ctx
    result = await crud.record_medicine_taken(session, medicine_id, status="skipped")
    await msg.edit_text(get_text(lang, "med_skipped", days=result.get("remaining_days", 0)), parse_mode="HTML")


@router.message(RestockMedicine.waiting_for_amount)
async def restock_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        return
    data = await state.get_data()
    medicine_id = data["medicine_id"]
    lang = data.get("lang", "ua")
    needs_take = data.get("needs_take", False)

    amount = parse_int(message.text.strip())
    if amount is None:
        await message.answer(get_text(lang, "err_stock"))
        return

    new_stock = await crud.add_stock(session, medicine_id, amount)

    if needs_take:
        result = await crud.record_medicine_taken(session, medicine_id, status="taken")
        new_stock = result.get("stock_amount", new_stock)

    if message.from_user:
        logger.info(
            f"User {message.from_user.id} (@{message.from_user.username}) restocked medicine (id={medicine_id}) by {amount}, new stock={new_stock}"
        )

    await state.clear()
    await message.answer(get_text(lang, "restock_success", amount=new_stock), parse_mode="HTML")
