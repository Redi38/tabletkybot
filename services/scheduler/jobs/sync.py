"""
Adding/removing per-medicine cron jobs, and full/single sync between the
database and the in-memory job queue.
"""

import logging

from aiogram import Bot
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from database.models import Medicine, User
from locales.texts import DEFAULT_LANG, user_lang

from .core import _MED_JOB_PREFIX, _med_job_id, _next_schedule_id_for_today, scheduler
from .reminders import remove_reminders, send_reminder

logger = logging.getLogger(__name__)


def add_reminders_for_medicine(
    bot: Bot,
    medicine: Medicine,
    timezone: str,
    chat_id: int,
    language: str = DEFAULT_LANG,
    is_sync: bool = False,
    session_factory: async_sessionmaker | None = None,
) -> None:
    if not medicine.is_active:
        remove_reminders(medicine.id)
        return

    count = 0

    for sched in medicine.schedules:
        try:
            hour, minute = map(int, sched.scheduled_time.split(":"))
            job_id = _med_job_id(medicine.id, sched.id)

            if scheduler.get_job(job_id) is not None:
                count += 1
                continue

            scheduler.add_job(
                send_reminder,
                trigger=CronTrigger(hour=hour, minute=minute, timezone=timezone),
                id=job_id,
                replace_existing=True,
                misfire_grace_time=60,
                kwargs={
                    "bot": bot,
                    "medicine_id": medicine.id,
                    "medicine_name": medicine.name,
                    "chat_id": chat_id,
                    "course_duration": medicine.course_duration,
                    "language": language,
                    "timezone": timezone,
                    "schedule_id": sched.id,
                    "session_factory": session_factory,
                },
            )
            count += 1
            if not is_sync:
                logger.info(f"Reminder {job_id} set for {sched.scheduled_time} ({timezone})")
        except Exception as e:
            logger.error(f"Error adding reminder {sched.scheduled_time}: {e}")

    if not is_sync and count:
        logger.info(f"Set {count} reminders for {medicine.name}")


async def pause_daily_reminders_for_user(session: AsyncSession, chat_id: int) -> int:
    """
    Called the moment a user blocks the bot (via the `my_chat_member` event
    in handlers/bot_status.py). Cancels the daily per-schedule cron jobs for
    all of that user's active medicines immediately, instead of leaving them
    to keep firing and getting lazily skipped one by one whenever they next
    happen to run.
    """
    from database import crud

    medicines = await crud.get_user_medicines(session, chat_id, active_only=True)
    paused = 0
    for med in medicines:
        for sched in med.schedules:
            job_id = _med_job_id(med.id, sched.id)
            try:
                scheduler.remove_job(job_id)
                paused += 1
            except Exception:
                pass
    if paused:
        logger.info(f"Paused {paused} daily reminder job(s) for user {chat_id} (bot blocked)")
    return paused


async def resume_daily_reminders_for_user(bot: Bot, session_factory: async_sessionmaker, chat_id: int) -> int:
    """
    Called the moment a user unblocks the bot. Recreates the daily
    per-schedule cron jobs for all of that user's active medicines —
    add_reminders_for_medicine() is idempotent (skips a schedule if its job
    already exists), so this is safe to call even if some jobs were never
    actually removed.
    """
    from database import crud

    async with session_factory() as session:
        language = await crud.get_user_language(session, chat_id)
        timezone = await crud.get_user_timezone(session, chat_id) or "Europe/Kyiv"
        medicines = await crud.get_user_medicines(session, chat_id, active_only=True)

    resumed = 0
    for med in medicines:
        before = len(med.schedules)
        add_reminders_for_medicine(
            bot=bot,
            medicine=med,
            timezone=timezone,
            chat_id=chat_id,
            language=language,
            is_sync=True,
            session_factory=session_factory,
        )
        resumed += before
    if resumed:
        logger.info(f"Resumed daily reminders for {len(medicines)} medicine(s) for user {chat_id} (bot unblocked)")
    return resumed


async def sync_reminders(bot: Bot, session_factory: async_sessionmaker) -> None:
    """Full synchronization of the DB and the scheduler's in-memory state."""
    from database import crud

    expected_ids: set[str] = set()
    active_data: list[tuple[Medicine, User]] = []

    async with session_factory() as session:
        users = await crud.get_all_users(session)
        for user in users:
            if user.is_blocked:
                continue
            medicines = await crud.get_user_medicines(session, user.id, active_only=True)
            for med in medicines:
                active_data.append((med, user))
                for sched in med.schedules:
                    expected_ids.add(_med_job_id(med.id, sched.id))

    for job in scheduler.get_jobs():
        if job.id.startswith(_MED_JOB_PREFIX) and job.id not in expected_ids:
            scheduler.remove_job(job.id)

    for med, user in active_data:
        add_reminders_for_medicine(
            bot=bot,
            medicine=med,
            timezone=user.timezone or "Europe/Kyiv",
            chat_id=user.id,
            language=user_lang(user),
            is_sync=True,
            session_factory=session_factory,
        )

    logger.info(f"Successfully restored {len(expected_ids)} reminders from the database!")


async def sync_single_reminder(bot: Bot, session_factory: async_sessionmaker, medicine_id: int, action: str) -> None:
    if action == "delete":
        remove_reminders(medicine_id)
        return

    async with session_factory() as session:
        query = (
            select(Medicine, User)
            .join(User, Medicine.user_id == User.id)
            .options(selectinload(Medicine.schedules))
            .where(Medicine.id == medicine_id)
        )
        result = await session.execute(query)
        row = result.first()

    if not row:
        remove_reminders(medicine_id)
        return

    med, user = row

    if action == "send_now":
        schedule_id = _next_schedule_id_for_today(med.schedules, user.timezone or "Europe/Kyiv")
        await send_reminder(
            bot=bot,
            medicine_id=med.id,
            medicine_name=med.name,
            chat_id=user.id,
            course_duration=med.course_duration,
            language=user_lang(user),
            timezone=user.timezone or "Europe/Kyiv",
            is_manual=True,
            schedule_id=schedule_id,
            session_factory=session_factory,
        )
        logger.info(f"Immediate reminder sent for med_{medicine_id} (requested from the Admin Panel)")
        return

    add_reminders_for_medicine(
        bot=bot,
        medicine=med,
        timezone=user.timezone or "Europe/Kyiv",
        chat_id=user.id,
        language=user_lang(user),
        is_sync=True,
        session_factory=session_factory,
    )
    logger.info(f"Schedules updated for med_{medicine_id}")
