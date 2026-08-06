"""
Sending reminders: the initial dose reminder (send_reminder) and the hourly
repeat that keeps nudging until the user taps Taken/Skip (send_repeat_reminder).

Depends on redis_state.py for tracking which reminders are unacknowledged
(so the hourly repeat knows what to resend) and pending stock alerts (so a
reminder firing after an unacknowledged empty-stock alert can auto-archive
the medicine instead of sending a normal dose reminder).
"""

import logging

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
from .utils import _handle_user_blocked, _local_today, _manual_reminder_today, get_reminder_keyboard

logger = logging.getLogger(__name__)


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
        )
        logger.info(f"Repeat reminder sent to {chat_id} for {medicine_name}")
    except TelegramForbiddenError:
        await _handle_user_blocked(chat_id, session_factory)
    except Exception as e:
        logger.error(f"Repeat reminder error for {chat_id}: {e}")
