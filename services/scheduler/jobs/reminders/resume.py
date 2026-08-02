"""
Pausing and resuming hourly repeat reminders: the live toggle path (user
flips the repeat-reminders setting in Settings) and the bot-startup restore
path (after a restart, based on what's still pending in Redis).
"""

import logging
from datetime import datetime
from datetime import timezone as dt_timezone

from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker

from ...redis_state import _delete_pending_reminder, _get_all_pending_reminders, _get_pending_reminders_for_chat
from ..core import scheduler
from .send import send_repeat_reminder
from .utils import _next_grid_slot

logger = logging.getLogger(__name__)


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

    blocked_chat_ids: set[int] = set()
    if session_factory is not None:
        from database import crud

        async with session_factory() as session:
            for chat_id, _medicine_id, _data in pending_list:
                if chat_id in blocked_chat_ids:
                    continue
                if await crud.get_user_blocked(session, chat_id):
                    blocked_chat_ids.add(chat_id)

    for chat_id, medicine_id, data in pending_list:
        if chat_id in blocked_chat_ids:
            # Stale from before this cleanup existed (or before the user
            # was marked blocked) — it'll never be acknowledged, so drop it
            # instead of restoring a repeat job that will just be skipped.
            await _delete_pending_reminder(chat_id, medicine_id)
            continue

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
