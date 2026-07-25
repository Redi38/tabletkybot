"""
Sending reminders (initial + hourly repeats), resuming them after a
restart, and removing all jobs (including repeats) for a given medicine.

Depends on redis_state.py for tracking which reminders are unacknowledged
(so the hourly repeat knows what to resend) and pending stock alerts (so a
reminder firing after an unacknowledged empty-stock alert can auto-archive
the medicine instead of sending a normal dose reminder).
"""

import asyncio
import logging
from datetime import date as date_type
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import async_sessionmaker

from locales.texts import get_text

from ..redis_state import (
    _delete_pending_reminder,
    _delete_pending_reminders_for_medicine,
    _delete_stock_alerts_for_medicine,
    _get_all_pending_reminders,
    _get_pending_reminder,
    _save_pending_reminder,
    clear_stock_alert_pending,
    get_stock_alert_pending,
)
from .core import _MED_JOB_PREFIX, scheduler

logger = logging.getLogger(__name__)

_manual_reminder_today: dict[tuple[int, int], date_type] = {}


def _local_today(tz_name: str) -> date_type:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Europe/Kyiv")
    return datetime.now(tz).date()


def get_reminder_keyboard(medicine_id: int, language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=get_text(language, "btn_take"), callback_data=f"take_{medicine_id}"),
                InlineKeyboardButton(text=get_text(language, "btn_skip"), callback_data=f"skip_{medicine_id}"),
            ]
        ]
    )


async def send_reminder(
    bot: Bot,
    medicine_id: int,
    medicine_name: str,
    chat_id: int,
    course_duration: int,
    language: str,
    timezone: str = "Europe/Kyiv",
    is_manual: bool = False,
    schedule_id: int | None = None,
    session_factory: async_sessionmaker | None = None,
) -> None:
    # ── Auto-archive check ──────────────────────────────────────────────
    # If the empty-stock alert from the previous dose is still unacknowledged
    # (user never pressed "Restock" or "Archive"), archive the medicine now
    # instead of sending a regular reminder for a medicine with no stock left.
    if session_factory is not None and not is_manual:
        stock_alert = await get_stock_alert_pending(chat_id, medicine_id)
        if stock_alert:
            from database import crud

            async with session_factory() as session:
                await crud.update_medicine_field(session, medicine_id, "is_active", False)
            remove_reminders(medicine_id)
            await clear_stock_alert_pending(chat_id, medicine_id)
            lang = stock_alert.get("language", language)
            name = stock_alert.get("medicine_name", medicine_name)
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=get_text(lang, "med_auto_archived_no_action", name=name),
                    parse_mode="HTML",
                )
                logger.info(
                    f"Medicine '{name}' (id={medicine_id}) auto-archived for user {chat_id} "
                    f"— no action taken on the empty-stock alert before the next dose"
                )
            except Exception as e:
                logger.error(f"Error sending auto-archive notification to {chat_id}: {e}")
            return

    today = _local_today(timezone)

    if is_manual:
        if schedule_id is not None:
            _manual_reminder_today[(medicine_id, schedule_id)] = today
    elif schedule_id is not None and _manual_reminder_today.get((medicine_id, schedule_id)) == today:
        logger.info(
            f"Skipping the regular reminder for med_{medicine_id} schedule_{schedule_id} "
            f"— already sent manually today via the Admin Panel"
        )
        _manual_reminder_today.pop((medicine_id, schedule_id), None)
        return

    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=get_text(language, "remind_text", name=medicine_name, days=course_duration),
            reply_markup=get_reminder_keyboard(medicine_id, language),
            parse_mode="HTML",
        )
        logger.info(f"Reminder sent to user {chat_id} for {medicine_name}")

        await _save_pending_reminder(
            chat_id,
            medicine_id,
            sent.message_id,
            medicine_name,
            course_duration,
            language,
            timezone,
        )

        repeat_enabled = True
        if session_factory is not None:
            from database import crud

            async with session_factory() as session:
                repeat_enabled = await crud.get_repeat_reminders_enabled(session, chat_id)

        if repeat_enabled:
            scheduler.add_job(
                send_repeat_reminder,
                trigger="interval",
                hours=1,
                id=f"repeat_{medicine_id}_{chat_id}",
                replace_existing=True,
                misfire_grace_time=300,
                kwargs={"bot": bot, "medicine_id": medicine_id, "chat_id": chat_id},
            )
        else:
            logger.info(f"Repeat reminders disabled by user {chat_id} — not scheduling repeat_{medicine_id}_{chat_id}")
    except Exception as e:
        logger.error(f"Error sending reminder to user {chat_id}: {e}")


async def send_repeat_reminder(bot: Bot, medicine_id: int, chat_id: int) -> None:
    """
    Repeat reminder every hour until the button is pressed.
    Each time it deletes the PREVIOUS message and sends a NEW one instead
    of it — so the reminder always pops up at the bottom of the chat instead of
    getting lost among old repeats.
    """
    pending = await _get_pending_reminder(chat_id, medicine_id)
    if not pending:
        try:
            scheduler.remove_job(f"repeat_{medicine_id}_{chat_id}")
        except Exception:
            pass
        return

    language = pending["language"]
    medicine_name = pending["medicine_name"]

    try:
        await bot.delete_message(chat_id=chat_id, message_id=pending["message_id"])
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.warning(f"Failed to delete the previous reminder {pending['message_id']}: {e}")

    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=get_text(language, "remind_repeat_text", name=medicine_name),
            reply_markup=get_reminder_keyboard(medicine_id, language),
            parse_mode="HTML",
        )
        await _save_pending_reminder(
            chat_id,
            medicine_id,
            sent.message_id,
            medicine_name,
            pending["course_duration"],
            language,
            pending["timezone"],
        )
        logger.info(f"Repeat reminder sent to {chat_id} for {medicine_name}")
    except Exception as e:
        logger.error(f"Repeat reminder error for {chat_id}: {e}")


async def cancel_repeat_reminder(chat_id: int, medicine_id: int) -> None:
    try:
        scheduler.remove_job(f"repeat_{medicine_id}_{chat_id}")
        logger.info(f"Repeat reminder repeat_{medicine_id}_{chat_id} cancelled")
    except Exception:
        pass
    await _delete_pending_reminder(chat_id, medicine_id)


async def resume_pending_reminders(bot: Bot) -> None:
    """
    Called ONCE at bot startup (after sync_reminders). Restores hourly
    repeat jobs for all reminders the user hasn't confirmed yet, preserving
    the original hourly cadence based on when the reminder/repeat was last
    sent (via the "sent_at" timestamp stored in Redis) — instead of
    resetting the 1-hour countdown to "now + 1 hour" on every restart, which
    causes the repeat to drift later and later with each restart.
    """
    pending_list = await _get_all_pending_reminders()
    restored = 0
    now = datetime.now(dt_timezone.utc)

    for chat_id, medicine_id, data in pending_list:
        job_id = f"repeat_{medicine_id}_{chat_id}"
        if scheduler.get_job(job_id):
            continue

        next_run_time = None
        sent_at_str = data.get("sent_at")
        if sent_at_str:
            try:
                sent_at = datetime.fromisoformat(sent_at_str)
                if sent_at.tzinfo is None:
                    sent_at = sent_at.replace(tzinfo=dt_timezone.utc)
                next_run_time = sent_at + timedelta(hours=1)
                if next_run_time < now:
                    next_run_time = now
            except ValueError:
                next_run_time = None

        scheduler.add_job(
            send_repeat_reminder,
            trigger="interval",
            hours=1,
            id=job_id,
            replace_existing=True,
            next_run_time=next_run_time,
            misfire_grace_time=300,
            kwargs={"bot": bot, "medicine_id": medicine_id, "chat_id": chat_id},
        )
        restored += 1

    if restored:
        logger.info(f"Restored {restored} unfinished hourly reminders after restart")


def remove_reminders(medicine_id: int) -> None:
    prefix = f"{_MED_JOB_PREFIX}{medicine_id}_"
    repeat_prefix = f"repeat_{medicine_id}_"
    removed = 0

    for job in scheduler.get_jobs():
        if job.id.startswith(prefix) or job.id.startswith(repeat_prefix):
            try:
                scheduler.remove_job(job.id)
                removed += 1
            except Exception as e:
                logger.error(f"Error removing reminder {job.id}: {e}")

    for key in [k for k in _manual_reminder_today if k[0] == medicine_id]:
        _manual_reminder_today.pop(key, None)

    try:
        asyncio.create_task(_delete_pending_reminders_for_medicine(medicine_id))
        asyncio.create_task(_delete_stock_alerts_for_medicine(medicine_id))
    except RuntimeError:
        logger.warning(f"No active event loop to clean up Redis for med_{medicine_id}")

    if removed:
        logger.info(f"Removed {removed} schedules (including repeats) for medicine ID {medicine_id}")
