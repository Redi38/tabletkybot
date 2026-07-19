"""Handlers for marking a prescription purchase."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from locales.texts import data_lang, get_text

from .keyboards import stock_ask_kb
from .states import BuyPrescription
from .utils import _base_ctx, _valid_prescription_ctx, parse_optional_int

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("presc_buy_ask_"))
async def buy_ask_amount(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    ctx = await _valid_prescription_ctx(call, session)
    if not ctx:
        return
    msg, lang, prescription_id, prescription = ctx
    await state.update_data(prescription_id=prescription_id, lang=lang)

    if prescription.max_quantity is not None:
        remaining = prescription.max_quantity - prescription.purchased_quantity
        text = get_text(lang, "ask_bought_amount_limit", remaining=remaining)
    else:
        text = get_text(lang, "ask_bought_amount")

    await msg.answer(text, parse_mode="HTML")
    await state.set_state(BuyPrescription.waiting_amount)
    await call.answer()


@router.message(BuyPrescription.waiting_amount)
async def buy_amount_entered(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if not message.text:
        return
    data = await state.get_data()
    lang = data_lang(data)
    prescription_id = data["prescription_id"]

    amount = parse_optional_int(message.text)
    if amount is None or amount == -1 or amount <= 0:
        await message.answer(get_text(lang, "err_stock"), parse_mode="HTML")
        return

    prescription = await crud.get_prescription_by_id(session, prescription_id)
    if not prescription:
        await state.clear()
        return

    # ── Validate against the prescription limit ──────────────────────────
    if prescription.max_quantity is not None:
        remaining = prescription.max_quantity - prescription.purchased_quantity
        if amount > remaining:
            await message.answer(
                get_text(lang, "err_exceeds_prescription_limit", remaining=remaining),
                parse_mode="HTML",
            )
            return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=get_text(lang, "btn_confirm_bought"),
                    callback_data=f"presc_buy_confirm_{prescription_id}_{amount}",
                    style="success",
                ),
                InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="presc_list"),
            ]
        ]
    )
    await message.answer(
        get_text(lang, "presc_bought_confirm", amount=amount, name=prescription.medicine_name),
        reply_markup=kb,
        parse_mode="HTML",
    )
    await state.clear()


@router.callback_query(F.data.startswith("presc_buy_confirm_"))
async def buy_confirm(call: CallbackQuery, session: AsyncSession) -> None:
    ctx = await _base_ctx(call, session)
    if not ctx or not call.data:
        return
    msg, lang = ctx
    parts = str(call.data).split("_")
    prescription_id, amount = int(parts[-2]), int(parts[-1])

    result = await crud.mark_prescription_purchased(session, prescription_id, amount)
    if not result.get("success"):
        return

    logger.info(
        f"User {call.from_user.id} (@{call.from_user.username}) marked {amount} unit(s) "
        f"bought for prescription '{result['medicine_name']}' (id={prescription_id})"
    )

    await msg.edit_text(
        get_text(
            lang,
            "presc_bought_success",
            purchased=result["purchased_quantity"],
            max=result["max_quantity"] if result["max_quantity"] is not None else "∞",
        ),
        parse_mode="HTML",
    )

    await msg.answer(
        get_text(lang, "presc_ask_add_to_stock"),
        reply_markup=stock_ask_kb(prescription_id, amount, lang),
        parse_mode="HTML",
    )

    if result.get("is_fully_purchased"):
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=get_text(lang, "btn_presc_archive_now"),
                        callback_data=f"presc_finish_archive_{prescription_id}",
                        style="danger",
                    ),
                    InlineKeyboardButton(
                        text=get_text(lang, "btn_presc_keep_active"),
                        callback_data=f"presc_finish_keep_{prescription_id}",
                        style="success",
                    ),
                ]
            ]
        )
        await msg.answer(
            get_text(lang, "presc_fully_purchased_ask", name=result["medicine_name"]),
            reply_markup=kb,
            parse_mode="HTML",
        )
    await call.answer()
