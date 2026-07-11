import asyncio
import json
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta, timezone as dt_timezone, date as date_type
from zoneinfo import ZoneInfo
import redis.asyncio as aioredis

from locales.texts import get_text
from database.models import Medicine, User, Prescription

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

_MED_JOB_PREFIX = "med_"

_manual_reminder_today: dict[int, date_type] = {}

_redis_client: aioredis.Redis | None = None
_PENDING_KEY_PREFIX = "pending_reminder:"
_PENDING_TTL_SECONDS = 60 * 60 * 48

_STOCK_ALERT_KEY_PREFIX = "stock_alert_pending:"
_STOCK_ALERT_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 дней


def init_redis(redis_url: str) -> None:
    global _redis_client
    _redis_client = aioredis.from_url(redis_url, decode_responses=True)


def _pending_key(chat_id: int, medicine_id: int) -> str:
    return f"{_PENDING_KEY_PREFIX}{chat_id}:{medicine_id}"


async def _save_pending_reminder(
        chat_id: int, medicine_id: int, message_id: int,
        medicine_name: str, course_duration: int, language: str, timezone: str,
) -> None:
    if not _redis_client:
        return
    data = {
        "message_id": message_id,
        "medicine_name": medicine_name,
        "course_duration": course_duration,
        "language": language,
        "timezone": timezone,
        "sent_at": datetime.now(dt_timezone.utc).isoformat(),
    }
    await _redis_client.set(_pending_key(chat_id, medicine_id), json.dumps(data), ex=_PENDING_TTL_SECONDS)


async def _get_pending_reminder(chat_id: int, medicine_id: int) -> dict | None:
    if not _redis_client:
        return None
    raw = await _redis_client.get(_pending_key(chat_id, medicine_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def _delete_pending_reminder(chat_id: int, medicine_id: int) -> None:
    if not _redis_client:
        return
    await _redis_client.delete(_pending_key(chat_id, medicine_id))


async def _get_all_pending_reminders() -> list[tuple[int, int, dict]]:
    """Returns [(chat_id, medicine_id, data), ...] — used to restore state on bot startup."""
    if not _redis_client:
        return []
    result = []
    async for key in _redis_client.scan_iter(match=f"{_PENDING_KEY_PREFIX}*"):
        try:
            _, chat_id_str, medicine_id_str = key.split(":")
            raw = await _redis_client.get(key)
            if raw:
                data = json.loads(raw)
                result.append((int(chat_id_str), int(medicine_id_str), data))
        except (ValueError, json.JSONDecodeError):
            continue
    return result


async def _delete_pending_reminders_for_medicine(medicine_id: int) -> None:
    if not _redis_client:
        return
    pattern = f"{_PENDING_KEY_PREFIX}*:{medicine_id}"
    async for key in _redis_client.scan_iter(match=pattern):
        await _redis_client.delete(key)
        logger.info(f"Deleted an orphaned pending Redis record: {key}")


def _stock_alert_key(chat_id: int, medicine_id: int) -> str:
    return f"{_STOCK_ALERT_KEY_PREFIX}{chat_id}:{medicine_id}"


async def save_stock_alert_pending(chat_id: int, medicine_id: int, medicine_name: str, language: str) -> None:
    """Marks that an 'empty stock' alert was sent and is awaiting a user action
    (restock or archive). If no action is taken before the next scheduled dose,
    the medicine will be auto-archived."""
    if not _redis_client:
        return
    data = {"medicine_name": medicine_name, "language": language}
    await _redis_client.set(_stock_alert_key(chat_id, medicine_id), json.dumps(data), ex=_STOCK_ALERT_TTL_SECONDS)


async def get_stock_alert_pending(chat_id: int, medicine_id: int) -> dict | None:
    if not _redis_client:
        return None
    raw = await _redis_client.get(_stock_alert_key(chat_id, medicine_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def clear_stock_alert_pending(chat_id: int, medicine_id: int) -> None:
    if not _redis_client:
        return
    await _redis_client.delete(_stock_alert_key(chat_id, medicine_id))


async def _delete_stock_alerts_for_medicine(medicine_id: int) -> None:
    if not _redis_client:
        return
    pattern = f"{_STOCK_ALERT_KEY_PREFIX}*:{medicine_id}"
    async for key in _redis_client.scan_iter(match=pattern):
        await _redis_client.delete(key)


def _local_today(tz_name: str) -> date_type:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Europe/Kyiv")
    return datetime.now(tz).date()


def _med_job_id(medicine_id: int, schedule_id: int) -> str:
    return f"{_MED_JOB_PREFIX}{medicine_id}_{schedule_id}"


def get_reminder_keyboard(medicine_id: int, language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=get_text(language, "btn_take"), callback_data=f"take_{medicine_id}"),
        InlineKeyboardButton(text=get_text(language, "btn_skip"), callback_data=f"skip_{medicine_id}"),
    ]])


async def send_reminder(
        bot: Bot, medicine_id: int, medicine_name: str,
        chat_id: int, course_duration: int, language: str,
        timezone: str = "Europe/Kyiv", is_manual: bool = False,
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
        _manual_reminder_today[medicine_id] = today
    elif _manual_reminder_today.get(medicine_id) == today:
        logger.info(
            f"Skipping the regular reminder for med_{medicine_id} — "
            f"already sent manually today via the Admin Panel"
        )
        _manual_reminder_today.pop(medicine_id, None)
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
            chat_id, medicine_id, sent.message_id,
            medicine_name, course_duration, language, timezone,
        )

        scheduler.add_job(
            send_repeat_reminder,
            trigger="interval",
            hours=1,
            id=f"repeat_{medicine_id}_{chat_id}",
            replace_existing=True,
            kwargs={"bot": bot, "medicine_id": medicine_id, "chat_id": chat_id},
        )
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
            chat_id, medicine_id, sent.message_id,
            medicine_name, pending["course_duration"], language, pending["timezone"],
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

    _manual_reminder_today.pop(medicine_id, None)

    try:
        asyncio.create_task(_delete_pending_reminders_for_medicine(medicine_id))
        asyncio.create_task(_delete_stock_alerts_for_medicine(medicine_id))
    except RuntimeError:
        logger.warning(f"No active event loop to clean up Redis for med_{medicine_id}")

    if removed:
        logger.info(f"Removed {removed} schedules (including repeats) for medicine ID {medicine_id}")


def add_reminders_for_medicine(
        bot: Bot, medicine: Medicine, timezone: str,
        chat_id: int, language: str = "ua", is_sync: bool = False,
        session_factory: async_sessionmaker | None = None,
) -> None:
    if not medicine.is_active:
        remove_reminders(medicine.id)
        return

    existing_ids = {job.id for job in scheduler.get_jobs()}
    count = 0

    for sched in medicine.schedules:
        try:
            hour, minute = map(int, sched.scheduled_time.split(":"))
            job_id = _med_job_id(medicine.id, sched.id)

            if job_id in existing_ids:
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


def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown()


async def sync_reminders(bot: Bot, session_factory: async_sessionmaker) -> None:
    """Full synchronization of the DB and the scheduler's in-memory state."""
    from database import crud

    expected_ids: set[str] = set()
    active_data: list[tuple[Medicine, User]] = []

    async with session_factory() as session:
        users = await crud.get_all_users(session)
        for user in users:
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
            bot=bot, medicine=med,
            timezone=user.timezone or "Europe/Kyiv",
            chat_id=user.id,
            language=user.language or "ua",
            is_sync=True,
            session_factory=session_factory,
        )

    logger.info(f"Successfully restored {len(expected_ids)} reminders from the database!")


async def sync_single_reminder(
        bot: Bot, session_factory: async_sessionmaker, medicine_id: int, action: str
) -> None:
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
        await send_reminder(
            bot=bot,
            medicine_id=med.id,
            medicine_name=med.name,
            chat_id=user.id,
            course_duration=med.course_duration,
            language=user.language or "ua",
            timezone=user.timezone or "Europe/Kyiv",
            is_manual=True,
        )
        logger.info(f"Immediate reminder sent for med_{medicine_id} (requested from the Admin Panel)")
        return

    add_reminders_for_medicine(
        bot=bot, medicine=med,
        timezone=user.timezone or "Europe/Kyiv",
        chat_id=user.id,
        language=user.language or "ua",
        is_sync=True,
        session_factory=session_factory,
    )
    logger.info(f"Schedules updated for med_{medicine_id}")


def get_prescription_alert_keyboard(prescription_id: int, language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=get_text(language, "btn_mark_bought"),
            callback_data=f"presc_buy_ask_{prescription_id}",
        )
    ]])


async def check_prescription_reminders(bot: Bot, session_factory: async_sessionmaker) -> None:
    from database import crud

    async with session_factory() as session:
        pending = await crud.get_prescriptions_needing_reminder(session)

        for prescription, user in pending:
            try:
                tz = ZoneInfo(user.timezone or "Europe/Kyiv")
            except Exception:
                tz = ZoneInfo("Europe/Kyiv")

            local_now = datetime.now(tz)
            target_date = prescription.expires_at - timedelta(days=prescription.reminder_days_before)

            if local_now.date() != target_date or local_now.hour != 9:
                continue

            days_left = (prescription.expires_at - local_now.date()).days
            language = user.language or "ua"

            try:
                await bot.send_message(
                    chat_id=user.id,
                    text=get_text(
                        language, "presc_expiring_alert",
                        name=prescription.medicine_name,
                        expires=prescription.expires_at.strftime("%d.%m.%Y"),
                        days=days_left,
                    ),
                    reply_markup=get_prescription_alert_keyboard(prescription.id, language),
                    parse_mode="HTML",
                )
                await crud.mark_prescription_reminder_sent(session, prescription.id)
                logger.info(f"Prescription reminder {prescription.id} sent to {user.id}")
            except Exception as e:
                logger.error(f"Prescription reminder error {prescription.id}: {e}")


async def archive_expired_prescriptions(bot: Bot, session_factory: async_sessionmaker) -> None:
    from database import crud

    async with session_factory() as session:
        expired = await crud.get_expired_active_prescriptions(session)

        for prescription, user in expired:
            await crud.archive_prescription(session, prescription.id)
            language = user.language or "ua"
            try:
                await bot.send_message(
                    chat_id=user.id,
                    text=get_text(language, "presc_expired_auto_archived", name=prescription.medicine_name),
                    parse_mode="HTML",
                )
                logger.info(f"Prescription {prescription.id} auto-archived (expired)")
            except Exception as e:
                logger.error(f"Error notifying about auto-archiving prescription {prescription.id}: {e}")
