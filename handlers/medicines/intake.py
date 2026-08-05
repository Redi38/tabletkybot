"""Handlers for recording a dose as taken/skipped — from a reminder's
take_/skip_ buttons, from the user self-reporting a missed dose via
"Mark as taken today" (e.g. after restocking a medicine whose reminder
time already passed today), and from self-reporting an early dose taken
ahead of a still-pending reminder later the same day."""

import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database import crud
from database.models import Medicine
from locales.texts import get_text
from services.scheduler import (
    _local_today,
    _manual_reminder_today,
    _next_schedule_id_for_today,
    acquire_action_lock,
    cancel_repeat_reminder,
    save_stock_alert_pending,
)

from .utils import _safe_edit_text, _valid_medicine_ctx

router = Router()
logger = logging.getLogger(__name__)


async def _prompt_restock_before_take(
    msg: Message, state: FSMContext, lang: str, medicine_id: int, medicine_name: str
) -> None:
    """
    Shown whenever the user wants to log a "take" but stock_amount is
    already 0 — recording a take would push it negative, so route them
    through the restock flow first (which itself offers to mark today's
    dose taken right after the new amount is saved, via restock.py's
    `needs_take` flag).
    """
    await state.update_data(medicine_id=medicine_id, lang=lang)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=get_text(lang, "btn_restock_yes"), callback_data=f"restock_yes_{medicine_id}")],
            [InlineKeyboardButton(text=get_text(lang, "btn_restock_no"), callback_data=f"restock_no_{medicine_id}")],
        ]
    )
    await _safe_edit_text(
        msg, get_text(lang, "alert_empty_but_time", name=medicine_name), reply_markup=kb, parse_mode="HTML"
    )


async def _send_take_result_followup(
    msg: Message,
    lang: str,
    medicine_id: int,
    medicine_name: str,
    result: dict,
    action: str,
    from_user_id: int,
) -> None:
    """
    Shared follow-up after crud.record_medicine_taken(): course-finished
    prompt, or a success message plus a stock alert if applicable. Used by
    both the take_/skip_ reminder buttons and the "mark as taken now"
    self-service action, so the two stay in sync automatically.
    """
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
            msg, get_text(lang, "course_finished_ask", name=medicine_name), reply_markup=kb, parse_mode="HTML"
        )
        return

    success_key = "med_taken" if action == "take" else "med_skipped"
    await _safe_edit_text(msg, get_text(lang, success_key, days=remaining), parse_mode="HTML")

    if action != "take" or stock is None:
        return

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
        await msg.answer(get_text(lang, "alert_empty_stock", name=medicine_name), reply_markup=kb, parse_mode="HTML")
        await save_stock_alert_pending(from_user_id, medicine_id, medicine_name, lang)
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
            get_text(lang, "alert_low_stock", name=medicine_name, amount=stock), reply_markup=kb, parse_mode="HTML"
        )


# ── Take / Skip (from a reminder message) ────────────────────────────────
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
        await _prompt_restock_before_take(msg, state, lang, medicine_id, str(medicine.name))
        return

    db_status = "taken" if action == "take" else "skipped"
    result = await crud.record_medicine_taken(session, medicine_id, status=db_status)
    await _send_take_result_followup(msg, lang, medicine_id, str(medicine.name), result, action, call.from_user.id)


# ── Mark as taken now (self-service, no reminder involved) ───────────────
async def _mark_taken(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> tuple[Message, Medicine] | None:
    """
    Shared body for both self-service "mark as taken" flows below: logs
    today's dose directly, skipping the take_/skip_ confirmation (pressing
    either always means "taken").
    """
    await call.answer()

    ctx = await _valid_medicine_ctx(call, session)
    if not ctx:
        return None
    msg, lang, medicine_id, medicine = ctx

    if not await acquire_action_lock(call.from_user.id, medicine_id):
        return None

    await cancel_repeat_reminder(call.from_user.id, medicine_id)

    if medicine.stock_amount is not None and medicine.stock_amount <= 0:
        await _prompt_restock_before_take(msg, state, lang, medicine_id, str(medicine.name))
        return None

    result = await crud.record_medicine_taken(session, medicine_id, status="taken")
    await _send_take_result_followup(msg, lang, medicine_id, str(medicine.name), result, "take", call.from_user.id)
    return msg, medicine


@router.callback_query(F.data.startswith("mark_taken_now_"))
async def mark_taken_missed(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """
    Catching up on a dose whose reminder time already passed today — e.g.
    after restocking a medicine that got archived, or any time the user
    simply missed pressing take/skip on the original reminder. No future
    reminder needs suppressing here: archiving already removed every job
    for this medicine, and restoring re-adds them via CronTrigger, which
    naturally schedules today's already-passed slot for tomorrow instead
    while keeping any later slot today intact.
    """
    if not call.from_user:
        return
    result = await _mark_taken(call, state, session)
    if result is None:
        return
    _, medicine = result
    logger.info(
        f"User {call.from_user.id} (@{call.from_user.username}) marked '{medicine.name}' "
        f"(id={medicine.id}) as taken directly (not via a reminder) — catching up on a missed dose"
    )


@router.callback_query(F.data.startswith("mark_taken_early_ask_"))
async def mark_taken_early_ask(call: CallbackQuery, session: AsyncSession) -> None:
    """
    Confirmation step shown before logging an early "taken today" dose from
    the medicine settings menu, so an accidental tap doesn't silently
    record a dose. Confirming re-fires the actual mark_taken_early_
    callback below; declining returns to the medicine's edit menu.
    """
    await call.answer()

    ctx = await _valid_medicine_ctx(call, session)
    if not ctx:
        return
    msg, lang, medicine_id, medicine = ctx

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=get_text(lang, "btn_yes"),
                    callback_data=f"mark_taken_early_{medicine_id}",
                    style="success",
                ),
                InlineKeyboardButton(
                    text=get_text(lang, "btn_no"),
                    callback_data=f"edit_med_{medicine_id}",
                ),
            ]
        ]
    )
    await _safe_edit_text(
        msg, get_text(lang, "mark_taken_confirm", name=str(medicine.name)), reply_markup=kb, parse_mode="HTML"
    )


@router.callback_query(F.data.regexp(re.compile(r"^mark_taken_early_\d+$")))
async def mark_taken_early(call: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """
    Logging a dose taken ahead of its scheduled reminder later today (e.g.
    took the pill at 12:00 with the bot's reminder set for 15:00). Unlike
    the "missed dose" case, the medicine is still active with a live job
    for the rest of today's schedule, so — to avoid a confusing (or, if
    acted on, dose-doubling) duplicate reminder — the soonest not-yet-fired
    schedule for today is suppressed once via the same one-time flag the
    Admin Panel's manual "Send Now" uses.
    """
    if not call.from_user:
        return

    result = await _mark_taken(call, state, session)
    if result is None:
        return
    _, medicine = result

    tz = await crud.get_user_timezone(session, call.from_user.id)
    schedule_id = _next_schedule_id_for_today(medicine.schedules, tz)
    if schedule_id is not None:
        _manual_reminder_today[(medicine.id, schedule_id)] = _local_today(tz)

    logger.info(
        f"User {call.from_user.id} (@{call.from_user.username}) marked '{medicine.name}' "
        f"(id={medicine.id}) as taken early — suppressing schedule_id={schedule_id} for today"
    )


@router.callback_query(F.data == "delete_message")
async def delete_alert_message(call: CallbackQuery) -> None:
    if isinstance(call.message, Message):
        await call.message.delete()
