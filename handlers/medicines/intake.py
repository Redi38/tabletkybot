"""Handlers for the take/skip reminder buttons."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from locales.texts import get_text
from services.scheduler import acquire_action_lock, cancel_repeat_reminder, save_stock_alert_pending

from .utils import _safe_edit_text, _valid_medicine_ctx

router = Router()
logger = logging.getLogger(__name__)


# ── Take / Skip ──────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("take_") | F.data.startswith("skip_"))
async def process_medicine_status(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await call.answer()

    ctx = await _valid_medicine_ctx(call, session)
    if not ctx or not call.data:
        return
    msg, lang, medicine_id, medicine = ctx

    if not await acquire_action_lock(call.from_user.id, medicine_id):
        return

    action = str(call.data).split("_")[0]

    logger.info(
        f"User {call.from_user.id} (@{call.from_user.username}) pressed '{action}' "
        f"for medicine '{medicine.name}' (id={medicine_id}) on message_id={msg.message_id}"
    )

    await cancel_repeat_reminder(call.from_user.id, medicine_id)

    if action == "take" and medicine.stock_amount is not None and medicine.stock_amount <= 0:
        await state.update_data(medicine_id=medicine_id, lang=lang)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=get_text(lang, "btn_restock_yes"), callback_data=f"restock_yes_{medicine_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=get_text(lang, "btn_restock_no"), callback_data=f"restock_no_{medicine_id}"
                    )
                ],
            ]
        )
        await _safe_edit_text(
            msg, get_text(lang, "alert_empty_but_time", name=str(medicine.name)), reply_markup=kb, parse_mode="HTML"
        )
        return

    db_status = "taken" if action == "take" else "skipped"
    result = await crud.record_medicine_taken(session, medicine_id, status=db_status)
    remaining = result.get("remaining_days", 0)
    stock = result.get("stock_amount")
    threshold = result.get("low_stock_threshold")

    if remaining <= 0:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=get_text(lang, "btn_course_continue"),
                        callback_data=f"med_extend_ask_{medicine_id}",
                        style="success",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=get_text(lang, "btn_course_finish"),
                        callback_data=f"med_archive_confirm_{medicine_id}",
                        style="primary",
                    )
                ],
            ]
        )
        await _safe_edit_text(
            msg, get_text(lang, "course_finished_ask", name=str(medicine.name)), reply_markup=kb, parse_mode="HTML"
        )
    else:
        success_key = "med_taken" if action == "take" else "med_skipped"
        await _safe_edit_text(msg, get_text(lang, success_key, days=remaining), parse_mode="HTML")

        if action == "take" and stock is not None:
            if stock == 0:
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=get_text(lang, "btn_add_stock"),
                                callback_data=f"restock_ask_{medicine_id}",
                                style="success",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text=get_text(lang, "btn_archive"),
                                callback_data=f"med_archive_confirm_{medicine_id}",
                                style="danger",
                            )
                        ],
                    ]
                )
                await msg.answer(
                    get_text(lang, "alert_empty_stock", name=str(medicine.name)), reply_markup=kb, parse_mode="HTML"
                )
                await save_stock_alert_pending(call.from_user.id, medicine_id, str(medicine.name), lang)
            elif threshold is not None and stock <= threshold:
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=get_text(lang, "btn_add_stock"), callback_data=f"restock_ask_{medicine_id}"
                            )
                        ],
                        [InlineKeyboardButton(text=get_text(lang, "btn_remind_later"), callback_data="delete_message")],
                    ]
                )
                await msg.answer(
                    get_text(lang, "alert_low_stock", name=str(medicine.name), amount=stock),
                    reply_markup=kb,
                    parse_mode="HTML",
                )


@router.callback_query(F.data == "delete_message")
async def delete_alert_message(call: CallbackQuery) -> None:
    if isinstance(call.message, Message):
        await call.message.delete()
