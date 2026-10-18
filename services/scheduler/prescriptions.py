"""
Prescription-related scheduled checks: reminders for prescriptions nearing
their expiry date (sent on every run — 06:00 and 15:00 UTC — from the first
reminder day until the prescription is fully purchased, archived or expired),
and auto-archiving ones that have expired without the user taking action.

Independent of both jobs.py and redis_state.py — these run as their own
periodic scheduler.add_job() calls (registered in main.py) rather than
per-medicine cron jobs, and don't touch Redis at all.
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import async_sessionmaker

from locales.texts import get_text, user_lang

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
            if user.is_blocked:
                continue

            try:
                tz = ZoneInfo(user.timezone or "Europe/Kyiv")
            except Exception:
                tz = ZoneInfo("Europe/Kyiv")

            local_today = datetime.now(tz).date()
            start_date = prescription.expires_at - timedelta(days=prescription.reminder_days_before)

            # Keep reminding on every run from the first reminder day through the
            # last valid day (expires_at inclusive); the query already excludes
            # fully purchased / archived prescriptions.
            if not (start_date <= local_today <= prescription.expires_at):
                continue

            days_left = (prescription.expires_at - local_today).days
            language = user_lang(user)

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
                await session.commit()
                logger.info(f"Prescription reminder {prescription.id} sent to {user.id}")
            except TelegramForbiddenError:
                await crud.mark_user_blocked(session, user.id)
                await session.commit()
                logger.info(f"User {user.id} has blocked the bot — marking blocked, skipping prescription reminder")
            except Exception as e:
                logger.error(f"Prescription reminder error {prescription.id}: {e}")


async def archive_expired_prescriptions(bot: Bot, session_factory: async_sessionmaker) -> None:
    from database import crud

    async with session_factory() as session:
        expired = await crud.get_expired_active_prescriptions(session)

        for prescription, user in expired:
            await crud.archive_prescription(session, prescription.id)
            # Persist before notifying: without this commit the archive was only
            # flushed and got rolled back on session close, while the user still
            # received the "auto-archived" message (and again on every nightly run).
            await session.commit()
            if user.is_blocked:
                continue
            language = user_lang(user)
            try:
                await bot.send_message(
                    chat_id=user.id,
                    text=get_text(language, "presc_expired_auto_archived", name=prescription.medicine_name),
                    parse_mode="HTML",
                )
                logger.info(f"Prescription {prescription.id} auto-archived (expired)")
            except TelegramForbiddenError:
                await crud.mark_user_blocked(session, user.id)
                await session.commit()
                logger.info(f"User {user.id} has blocked the bot — marking blocked, skipping archive notification")
            except Exception as e:
                logger.error(f"Error notifying about auto-archiving prescription {prescription.id}: {e}")
