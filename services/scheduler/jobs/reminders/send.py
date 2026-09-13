"""
Sending reminders: the initial dose reminder (send_reminder) and the hourly
repeat that keeps nudging until the user taps Taken/Skip (send_repeat_reminder).

Depends on redis_state.py for tracking which reminders are unacknowledged
(so the hourly repeat knows what to resend) and pending stock alerts (so a
reminder firing after an unacknowledged empty-stock alert can auto-archive
the medicine instead of sending a normal dose reminder).
"""

import logging
from datetime import datetime
from datetime import timezone as dt_timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import async_sessionmaker

from locales.texts import get_text

from ...redis_state import (
    _delete_pending_reminder,
    _get_pending_reminder,
    _save_pending_reminder,
    clear_stock_alert_pending,
    get_stock_alert_pending,
)
from ..core import _repeat_job_id, scheduler
from .remove import remove_reminders
from .utils import (
    _MAX_UNACKNOWLEDGED_DAYS,
    _handle_user_blocked,
    _local_today,
    _manual_reminder_today,
    _unacknowledged_duration,
    get_reminder_keyboard,
)

logger = logging.getLogger(__name__)


async def _archive_for_inactivity(
    bot: Bot,
    chat_id: int,
    medicine_id: int,
    medicine_name: str,
    language: str,
    days_unacknowledged: int,
    session_factory: async_sessionmaker | None,
) -> None:
    """
    Archives a medicine whose reminder has gone unacknowledged (no
    Taken/Skip tap) for _MAX_UNACKNOWLEDGED_DAYS or more: flips it inactive,
    cancels every scheduled job (daily + hourly repeat) and clears all
    Redis state for it via remove_reminders(), then lets the user know why
    it disappeared from their active list instead of leaving them wondering.
    """
    if session_factory is not None:
        from database import crud

        async with session_factory() as session:
            await crud.update_medicine_field(session, medicine_id, "is_active", False)
    remove_reminders(medicine_id)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=get_text(language, "med_auto_archived_inactivity", name=medicine_name, days=days_unacknowledged),
            parse_mode="HTML",
        )
        logger.info(
            f"Medicine '{medicine_name}' (id={medicine_id}) auto-archived for user {chat_id} "
            f"— no take/skip response for {days_unacknowledged}+ day(s)"
        )
    except TelegramForbiddenError:
        await _handle_user_blocked(chat_id, session_factory)
    except Exception as e:
        logger.error(f"Error sending inactivity auto-archive notification to {chat_id}: {e}")


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
                await _delete_pending_reminder(chat_id, medicine_id, schedule_id)
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

    # ── Inactivity auto-archive check ────────────────────────────────────
    # If the PREVIOUS dose reminder for this exact schedule slot is still
    # unacknowledged and has been so for _MAX_UNACKNOWLEDGED_DAYS or more,
    # archive the medicine now instead of sending yet another reminder that
    # will most likely go unanswered too. Without this check, send_reminder
    # would just silently overwrite the old pending entry (resetting the
    # unacknowledged streak) every time a new dose comes due — this also
    # covers users who have hourly repeat reminders turned off, since for
    # them the daily send_reminder call is the only recurring check point.
    existing_pending = None
    if session_factory is not None and not is_manual:
        existing_pending = await _get_pending_reminder(chat_id, medicine_id, schedule_id)
        if existing_pending:
            duration = _unacknowledged_duration(existing_pending, datetime.now(dt_timezone.utc))
            if duration is not None and duration.days >= _MAX_UNACKNOWLEDGED_DAYS:
                await _archive_for_inactivity(
                    bot, chat_id, medicine_id, medicine_name, language, duration.days, session_factory
                )
                return

    # ── Refresh course_duration from the DB ─────────────────────────────
    if session_factory is not None:
        from database import crud

        async with session_factory() as session:
            medicine = await crud.get_medicine_by_id(session, medicine_id)
            if medicine is not None:
                course_duration = medicine.course_duration

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
            reply_markup=get_reminder_keyboard(medicine_id, schedule_id, language),
            parse_mode="HTML",
        )
        logger.info(f"Reminder sent to user {chat_id} for {medicine_name}")

        await _save_pending_reminder(
            chat_id,
            medicine_id,
            schedule_id,
            sent.message_id,
            medicine_name,
            course_duration,
            language,
            timezone,
            first_sent_at=(existing_pending or {}).get("first_sent_at") or (existing_pending or {}).get("sent_at"),
        )

        repeat_enabled = True
        if session_factory is not None:
            from database import crud

            async with session_factory() as session:
                repeat_enabled = await crud.get_repeat_reminders_enabled(session, chat_id)

        repeat_job_id = _repeat_job_id(medicine_id, schedule_id, chat_id)
        if repeat_enabled:
            scheduler.add_job(
                send_repeat_reminder,
                trigger="interval",
                hours=1,
                id=repeat_job_id,
                replace_existing=True,
                misfire_grace_time=300,
                kwargs={
                    "bot": bot,
                    "medicine_id": medicine_id,
                    "schedule_id": schedule_id,
                    "chat_id": chat_id,
                    "session_factory": session_factory,
                },
            )
        else:
            logger.info(f"Repeat reminders disabled by user {chat_id} — not scheduling {repeat_job_id}")
    except TelegramForbiddenError:
        await _handle_user_blocked(chat_id, session_factory)
    except Exception as e:
        logger.error(f"Error sending reminder to user {chat_id}: {e}")


async def send_repeat_reminder(
    bot: Bot,
    medicine_id: int,
    chat_id: int,
    schedule_id: int | None = None,
    session_factory: async_sessionmaker | None = None,
) -> None:
    """
    Repeat reminder every hour until the button is pressed.
    Each time it deletes the PREVIOUS message and sends a NEW one instead
    of it — so the reminder always pops up at the bottom of the chat instead of
    getting lost among old repeats.
    """
    repeat_job_id = _repeat_job_id(medicine_id, schedule_id, chat_id)

    if session_factory is not None:
        from database import crud

        async with session_factory() as session:
            if await crud.get_user_blocked(session, chat_id):
                logger.info(f"Skipping repeat reminder for {chat_id} — user has blocked the bot")
                try:
                    scheduler.remove_job(repeat_job_id)
                except Exception:
                    pass
                await _delete_pending_reminder(chat_id, medicine_id, schedule_id)
                return

    pending = await _get_pending_reminder(chat_id, medicine_id, schedule_id)
    if not pending:
        try:
            scheduler.remove_job(repeat_job_id)
        except Exception:
            pass
        return

    language = pending["language"]
    medicine_name = pending["medicine_name"]

    # ── Inactivity auto-archive check ────────────────────────────────────
    # Same check as in send_reminder(), but reached via the hourly repeat
    # path instead of the daily one — catches users who do have repeat
    # reminders enabled well before the next day's dose would.
    if session_factory is not None:
        duration = _unacknowledged_duration(pending, datetime.now(dt_timezone.utc))
        if duration is not None and duration.days >= _MAX_UNACKNOWLEDGED_DAYS:
            try:
                scheduler.remove_job(repeat_job_id)
            except Exception:
                pass
            await _delete_pending_reminder(chat_id, medicine_id, schedule_id)
            await _archive_for_inactivity(
                bot, chat_id, medicine_id, medicine_name, language, duration.days, session_factory
            )
            return

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
            reply_markup=get_reminder_keyboard(medicine_id, schedule_id, language),
            parse_mode="HTML",
        )
        await _save_pending_reminder(
            chat_id,
            medicine_id,
            schedule_id,
            sent.message_id,
            medicine_name,
            pending["course_duration"],
            language,
            pending["timezone"],
            first_sent_at=pending.get("first_sent_at") or pending.get("sent_at"),
        )
        logger.info(f"Repeat reminder sent to {chat_id} for {medicine_name}")
    except TelegramForbiddenError:
        await _handle_user_blocked(chat_id, session_factory)
    except Exception as e:
        logger.error(f"Repeat reminder error for {chat_id}: {e}")
