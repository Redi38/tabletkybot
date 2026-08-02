"""
Small shared helpers used across the reminders package: the local-date
helper (for the "already sent manually today" dedup check), the reminder
keyboard, the hourly-grid alignment helper, and the blocked-user cleanup
routine used as a safety net when a send comes back with
TelegramForbiddenError.
"""

import logging
import math
from datetime import date as date_type
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import async_sessionmaker

from locales.texts import get_text

from ...redis_state import _delete_pending_reminder, _get_pending_reminders_for_chat
from ..core import scheduler

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
