"""
One-off / periodic sweep that catches medicines whose last recorded dose
(taken or skipped) — or, absent any record, whose creation date — is
already _MAX_UNACKNOWLEDGED_DAYS or more in the past.

The day-to-day check added to send_reminder()/send_repeat_reminder() can
only measure inactivity going FORWARD from when it starts running, because
it relies on Redis's pending-reminder state, which gets overwritten by
every resend — it can't tell how overdue an already-stale reminder actually
is. This sweep instead reads the durable MedicineRecord log via
crud.get_stale_active_medicines(), so it also catches medicines that were
already inactive before this feature was deployed. Run once at bot startup
(alongside sync_reminders/resume_pending_reminders) and again on a daily
cron, so it also catches anyone who goes quiet on a long-running process
that isn't restarted often.
"""

import logging
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker

from locales.texts import user_lang

from .send import _archive_for_inactivity
from .utils import _MAX_UNACKNOWLEDGED_DAYS

logger = logging.getLogger(__name__)


async def sweep_inactive_medicines(bot: Bot, session_factory: async_sessionmaker) -> int:
    """
    Archives every active medicine whose last activity (last taken/skipped
    dose, or creation date if it has none yet) is _MAX_UNACKNOWLEDGED_DAYS
    or more in the past. Returns how many medicines were archived.
    """
    from database import crud

    now = datetime.now(dt_timezone.utc)
    cutoff = now.replace(tzinfo=None) - timedelta(days=_MAX_UNACKNOWLEDGED_DAYS)

    async with session_factory() as session:
        stale = await crud.get_stale_active_medicines(session, cutoff)

    archived = 0
    for medicine, user, last_activity_at in stale:
        days_inactive = (now.replace(tzinfo=None) - last_activity_at).days
        await _archive_for_inactivity(
            bot,
            user.id,
            medicine.id,
            medicine.name,
            user_lang(user),
            days_inactive,
            session_factory,
        )
        archived += 1

    if archived:
        logger.info(f"Inactivity sweep archived {archived} medicine(s) with no take/skip response")
    return archived
