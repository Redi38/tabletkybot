"""
Prescription-related scheduled checks: nightly reminder for prescriptions
nearing their expiry date, and auto-archiving ones that have expired
without the user taking action.

Independent of both jobs.py and redis_state.py — these run as their own
periodic scheduler.add_job() calls (registered in main.py) rather than
per-medicine cron jobs, and don't touch Redis at all.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import async_sessionmaker

from locales.texts import get_text

logger = logging.getLogger(__name__)


def get_prescription_alert_keyboard(prescription_id: int, language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=get_text(language, "btn_mark_bought"),
                    callback_data=f"presc_buy_ask_{prescription_id}",
                )
            ]
        ]
    )


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
                        language,
                        "presc_expiring_alert",
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
