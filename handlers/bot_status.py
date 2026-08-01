"""
Tracks whether a user has blocked the bot, via Telegram's `my_chat_member`
update — sent the moment a user blocks or unblocks the bot in their
private chat with it. This is the authoritative signal: it's instant and
doesn't depend on guessing from a failed send somewhere else in the code.
"""

import logging

from aiogram import Bot, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.types import ChatMemberUpdated
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database import crud
from services.scheduler import (
    pause_daily_reminders_for_user,
    pause_repeat_reminders_for_user,
    resume_daily_reminders_for_user,
)

router = Router()
logger = logging.getLogger(__name__)


@router.my_chat_member()
async def track_bot_blocked_status(
    event: ChatMemberUpdated,
    session: AsyncSession,
    bot: Bot,
    session_factory: async_sessionmaker,
) -> None:
    if event.chat.type != ChatType.PRIVATE:
        return

    user_id = event.from_user.id
    if event.new_chat_member.status == ChatMemberStatus.KICKED:
        logger.info(f"User {user_id} (@{event.from_user.username}) blocked the bot")
        await crud.mark_user_blocked(session, user_id)
        await pause_repeat_reminders_for_user(user_id)
        await pause_daily_reminders_for_user(session, user_id)
    else:
        logger.info(
            f"User {user_id} (@{event.from_user.username}) (re)activated the bot (status={event.new_chat_member.status})"
        )
        await crud.mark_user_unblocked(session, user_id)
        await resume_daily_reminders_for_user(bot, session_factory, user_id)
