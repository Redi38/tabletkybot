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
import math
from datetime import date as date_type
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import async_sessionmaker

from locales.texts import get_text

from ..redis_state import (
    _delete_pending_reminder,
    _delete_pending_reminders_for_medicine,
    _delete_stock_alerts_for_medicine,
    _get_all_pending_reminders,
    _get_pending_reminder,
    _get_pending_reminders_for_chat,
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


async def _handle_user_blocked(chat_id: int, session_factory: async_sessionmaker | None) -> None:
    """
    Called the moment a send to `chat_id` comes back with
    TelegramForbiddenError (the user blocked the bot). This is a safety net
    for the normal detection path (the `my_chat_member` update in
    handlers/bot_status.py) — that update is instant and authoritative, but
    if it's ever missed or arrives late, this makes sure we still notice
    and stop hammering a user who can't receive messages, instead of
    retrying (and logging an "error") on every scheduled reminder forever.
    """
    logger.info(f"User {chat_id} has blocked the bot (detected via a failed send) — marking blocked and cancelling")
    if session_factory is not None:
        from database import crud

        async with session_factory() as session:
            await crud.mark_user_blocked(session, chat_id)
    for job in scheduler.get_jobs():
        if job.id.startswith("repeat_") and job.id.endswith(f"_{chat_id}"):
            try:
                scheduler.remove_job(job.id)
            except Exception:
                pass
    # Every pending reminder for this user is now stuck forever — no repeat
    # or future send will reach them while blocked — so clear them out of
    # the admin Reminder Queue instead of leaving stale entries behind.
    for medicine_id, _data in await _get_pending_reminders_for_chat(chat_id):
        await _delete_pending_reminder(chat_id, medicine_id)


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
    # ── Blocked-user fast path ──────────────────────────────────────────
    # If we already know (from a previous my_chat_member update, or a
    # previous send that hit TelegramForbiddenError below) that this user
    # blocked the bot, don't even attempt to send — there's no point
    # burning a Telegram API call, and it keeps this job from logging an
    # "error" every time it fires for a user who simply blocked the bot.
    if session_factory is not None:
        from database import crud

        async with session_factory() as session:
            if await crud.get_user_blocked(session, chat_id):
                logger.info(f"Skipping reminder for {chat_id} — user has blocked the bot")
                # Clean up any stale pending-reminder entry from before the
                # block (e.g. yesterday's dose was never acknowledged) so it
                # doesn't sit forever in the admin Reminder Queue — it can
                # never be resolved while the user is blocked.
                await _delete_pending_reminder(chat_id, medicine_id)
                return

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
            except TelegramForbiddenError:
                await _handle_user_blocked(chat_id, session_factory)
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
                kwargs={
                    "bot": bot,
                    "medicine_id": medicine_id,
                    "chat_id": chat_id,
                    "session_factory": session_factory,
                },
            )
        else:
            logger.info(f"Repeat reminders disabled by user {chat_id} — not scheduling repeat_{medicine_id}_{chat_id}")
    except TelegramForbiddenError:
        await _handle_user_blocked(chat_id, session_factory)
    except Exception as e:
        logger.error(f"Error sending reminder to user {chat_id}: {e}")


async def send_repeat_reminder(
    bot: Bot,
    medicine_id: int,
    chat_id: int,
    session_factory: async_sessionmaker | None = None,
) -> None:
    """
    Repeat reminder every hour until the button is pressed.
    Each time it deletes the PREVIOUS message and sends a NEW one instead
    of it — so the reminder always pops up at the bottom of the chat instead of
    getting lost among old repeats.
    """
    if session_factory is not None:
        from database import crud

        async with session_factory() as session:
            if await crud.get_user_blocked(session, chat_id):
                logger.info(f"Skipping repeat reminder for {chat_id} — user has blocked the bot")
                try:
                    scheduler.remove_job(f"repeat_{medicine_id}_{chat_id}")
                except Exception:
                    pass
                # It'll never be acknowledged while blocked — clear it so it
                # doesn't sit forever in the admin Reminder Queue.
                await _delete_pending_reminder(chat_id, medicine_id)
                return

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
    except TelegramForbiddenError:
        await _handle_user_blocked(chat_id, session_factory)
    except Exception as e:
        logger.error(f"Repeat reminder error for {chat_id}: {e}")


def _next_grid_slot(sent_at_str: str | None, now: datetime) -> datetime | None:
    """
    Given the original "sent_at" timestamp for a reminder, returns the next
    future slot on its hourly grid (sent_at, sent_at+1h, sent_at+2h, ...)
    that is still ahead of "now" — or "now" itself if it falls exactly on a
    slot. Returns None if "sent_at" is missing/unparseable, in which case
    the caller falls back to APScheduler's own default (now + 1 interval).
    """
    if not sent_at_str:
        return None
    try:
        sent_at = datetime.fromisoformat(sent_at_str)
    except ValueError:
        return None
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=dt_timezone.utc)
    elapsed_hours = (now - sent_at).total_seconds() / 3600
    next_slot = math.ceil(elapsed_hours) if elapsed_hours > 0 else 1
    return sent_at + timedelta(hours=next_slot)


async def cancel_repeat_reminder(chat_id: int, medicine_id: int) -> None:
    try:
        scheduler.remove_job(f"repeat_{medicine_id}_{chat_id}")
        logger.info(f"Repeat reminder repeat_{medicine_id}_{chat_id} cancelled")
    except Exception:
        pass
    await _delete_pending_reminder(chat_id, medicine_id)


async def pause_repeat_reminders_for_user(chat_id: int) -> int:
    """
    Called immediately when a user turns repeat reminders OFF. Stops the
    hourly re-nudge for every currently unacknowledged reminder belonging to
    this user, without touching the reminder message itself or its pending
    state — the user still sees the reminder they already got and can still
    tap Taken/Skip, it just won't be re-sent every hour anymore.
    """
    pending_list = await _get_pending_reminders_for_chat(chat_id)
    stopped = 0
    for medicine_id, _data in pending_list:
        try:
            scheduler.remove_job(f"repeat_{medicine_id}_{chat_id}")
            stopped += 1
        except Exception:
            pass
    if stopped:
        logger.info(f"Paused {stopped} active repeat reminder(s) for user {chat_id} (setting turned off)")
    return stopped


async def resume_repeat_reminders_for_user(
    bot: Bot, chat_id: int, session_factory: async_sessionmaker | None = None
) -> int:
    """
    Called immediately when a user turns repeat reminders back ON. Resumes
    the hourly cadence for every currently unacknowledged reminder belonging
    to this user, preserving the ORIGINAL hourly grid based on "sent_at"
    (e.g. reminder sent at 11:00 -> repeats would naturally land at 12:00,
    13:00, 14:00... -> if the user re-enables at 12:30, the next repeat is
    13:00, the next slot on that same grid) instead of resetting the
    countdown to "now + 1 hour". If "now" already sits exactly on (or past)
    a grid slot, the repeat fires immediately rather than waiting further.
    """
    pending_list = await _get_pending_reminders_for_chat(chat_id)
    resumed = 0
    now = datetime.now(dt_timezone.utc)
    for medicine_id, data in pending_list:
        job_id = f"repeat_{medicine_id}_{chat_id}"
        if scheduler.get_job(job_id):
            continue

        next_run_time = _next_grid_slot(data.get("sent_at"), now)

        scheduler.add_job(
            send_repeat_reminder,
            trigger="interval",
            hours=1,
            id=job_id,
            replace_existing=True,
            next_run_time=next_run_time,
            misfire_grace_time=300,
            kwargs={
                "bot": bot,
                "medicine_id": medicine_id,
                "chat_id": chat_id,
                "session_factory": session_factory,
            },
        )
        resumed += 1
    if resumed:
        logger.info(f"Resumed {resumed} repeat reminder(s) for user {chat_id} (setting turned back on)")
    return resumed


async def resume_pending_reminders(bot: Bot, session_factory: async_sessionmaker | None = None) -> None:
    """
    Called ONCE at bot startup (after sync_reminders). Restores hourly
    repeat jobs for all reminders the user hasn't confirmed yet, preserving
    the original hourly grid based on when the reminder was first sent (via
    the "sent_at" timestamp stored in Redis) — instead of resetting the
    1-hour countdown to "now + 1 hour" on every restart, which causes the
    repeat to drift later and later with each restart.
    """
    pending_list = await _get_all_pending_reminders()
    restored = 0
    now = datetime.now(dt_timezone.utc)

    for chat_id, medicine_id, data in pending_list:
        job_id = f"repeat_{medicine_id}_{chat_id}"
        if scheduler.get_job(job_id):
            continue

        next_run_time = _next_grid_slot(data.get("sent_at"), now)

        scheduler.add_job(
            send_repeat_reminder,
            trigger="interval",
            hours=1,
            id=job_id,
            replace_existing=True,
            next_run_time=next_run_time,
            misfire_grace_time=300,
            kwargs={
                "bot": bot,
                "medicine_id": medicine_id,
                "chat_id": chat_id,
                "session_factory": session_factory,
            },
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
