import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta, date as date_type
from zoneinfo import ZoneInfo

from locales.texts import get_text
from database.models import Medicine, User, Prescription

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

_MED_JOB_PREFIX = "med_"
_pending_reminders: dict[tuple[int, int], int] = {}

# Коли адмін тисне "Надіслати нагадування зараз" — фіксуємо тут дату
# (у ЛОКАЛЬНОМУ часовому поясі юзера) для цього medicine_id. Наступне
# спрацювання звичайного cron-job того ж дня перевіряє цю позначку і
# пропускає повторну відправку, щоб юзер не отримав два нагадування підряд.
_manual_reminder_today: dict[int, date_type] = {}


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
) -> None:
    from datetime import datetime, timedelta

    today = _local_today(timezone)

    if is_manual:
        # Ручна відправка з Адмін-Панелі — фіксуємо, що сьогодні для цього
        # препарату вже нагадали, щоб звичайний cron-job не продублював.
        _manual_reminder_today[medicine_id] = today
    elif _manual_reminder_today.get(medicine_id) == today:
        # Це звичайне спрацювання за розкладом, але сьогодні для цього
        # препарату вже було ручне нагадування — пропускаємо один раз.
        logger.info(
            f"Пропускаю звичайне нагадування для med_{medicine_id} — "
            f"сьогодні вже надіслано вручну через Адмін-Панель"
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
        logger.info(f"Нагадування відправлено користувачу {chat_id} для {medicine_name}")

        _pending_reminders[(chat_id, medicine_id)] = sent.message_id

        scheduler.add_job(
            send_repeat_reminder,
            trigger="interval",
            hours=1,
            id=f"repeat_{medicine_id}_{chat_id}",
            replace_existing=True,
            kwargs={
                "bot": bot,
                "medicine_id": medicine_id,
                "medicine_name": medicine_name,
                "chat_id": chat_id,
                "course_duration": course_duration,
                "language": language,
            },
        )
    except Exception as e:
        logger.error(f"Помилка відправки нагадування користувачу {chat_id}: {e}")


async def send_repeat_reminder(
        bot: Bot, medicine_id: int, medicine_name: str,
        chat_id: int, course_duration: int, language: str,
) -> None:
    """Повторне нагадування кожну годину поки не натиснута кнопка."""
    if (chat_id, medicine_id) not in _pending_reminders:
        return
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=get_text(language, "remind_repeat_text", name=medicine_name),
            parse_mode="HTML",
        )
        logger.info(f"Повторне нагадування відправлено {chat_id} для {medicine_name}")
    except Exception as e:
        logger.error(f"Помилка повторного нагадування {chat_id}: {e}")


def cancel_repeat_reminder(chat_id: int, medicine_id: int) -> None:
    """Викликається коли користувач натиснув кнопку."""
    _pending_reminders.pop((chat_id, medicine_id), None)
    try:
        scheduler.remove_job(f"repeat_{medicine_id}_{chat_id}")
        logger.info(f"Повторне нагадування repeat_{medicine_id}_{chat_id} скасовано")
    except Exception:
        pass

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
                logger.error(f"Помилка при видаленні нагадування {job.id}: {e}")

    stale_keys = [key for key in _pending_reminders if key[1] == medicine_id]
    for key in stale_keys:
        _pending_reminders.pop(key, None)

    _manual_reminder_today.pop(medicine_id, None)

    if removed:
        logger.info(f"Видалено {removed} розкладів (включно з повторами) для препарату ID {medicine_id}")


def add_reminders_for_medicine(
        bot: Bot, medicine: Medicine, timezone: str,
        chat_id: int, language: str = "ua", is_sync: bool = False,
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
                },
            )
            count += 1
            if not is_sync:
                logger.info(f"Нагадування {job_id} встановлено на {sched.scheduled_time} ({timezone})")
        except Exception as e:
            logger.error(f"Помилка при додаванні нагадування {sched.scheduled_time}: {e}")

    if not is_sync and count:
        logger.info(f"Встановлено {count} нагадувань для {medicine.name}")


def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown()


async def sync_reminders(bot: Bot, session_factory: async_sessionmaker) -> None:
    """Повна синхронізація БД та пам'яті планувальника."""
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

    # Видаляємо застарілі джоби
    for job in scheduler.get_jobs():
        if job.id.startswith(_MED_JOB_PREFIX) and job.id not in expected_ids:
            scheduler.remove_job(job.id)

    # Оновлюємо/додаємо актуальні
    for med, user in active_data:
        add_reminders_for_medicine(
            bot=bot, medicine=med,
            timezone=user.timezone or "Europe/Kyiv",
            chat_id=user.id,
            language=user.language or "ua",
            is_sync=True,
        )

    logger.info(f"Успішно відновлено {len(expected_ids)} нагадувань з бази даних!")


async def sync_single_reminder(
        bot: Bot, session_factory: async_sessionmaker, medicine_id: int, action: str
) -> None:
    """
    Точкове оновлення, видалення або НЕГАЙНА відправка нагадування для одного
    препарату. Викликається як з внутрішньої синхронізації БД, так і з
    Адмін-Панелі (кнопка "Надіслати нагадування зараз" шле action="send_now").
    """
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
        # Негайна відправка нагадування в обхід звичайного розкладу
        # APScheduler — саме те, чого раніше не вистачало для кнопки
        # "Надіслати нагадування зараз" в Адмін-Панелі.
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
        logger.info(f"Негайне нагадування відправлено для med_{medicine_id} (запит з Адмін-Панелі)")
        return

    add_reminders_for_medicine(
        bot=bot, medicine=med,
        timezone=user.timezone or "Europe/Kyiv",
        chat_id=user.id,
        language=user.language or "ua",
        is_sync=True,
    )
    logger.info(f"Оновлено розклади для med_{medicine_id}")

def get_prescription_alert_keyboard(prescription_id: int, language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=get_text(language, "btn_mark_bought"),
            callback_data=f"presc_buy_ask_{prescription_id}",
        )
    ]])


async def check_prescription_reminders(bot: Bot, session_factory: async_sessionmaker) -> None:
    """
    Запускається щогодини.
    Для кожного активного рецепту рахує ЛОКАЛЬНУ дату юзера (за його timezone)
    і надсилає нагадування рівно тоді, коли:
      - сьогодні (локально) = expires_at - reminder_days_before
      - зараз (локально) 9-та година
    """
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
                logger.info(f"Нагадування про рецепт {prescription.id} відправлено {user.id}")
            except Exception as e:
                logger.error(f"Помилка нагадування про рецепт {prescription.id}: {e}")


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
                logger.info(f"Рецепт {prescription.id} автоархівовано (термін минув)")
            except Exception as e:
                logger.error(f"Помилка сповіщення про автоархівацію рецепту {prescription.id}: {e}")
