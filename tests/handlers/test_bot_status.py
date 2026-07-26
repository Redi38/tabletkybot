"""
Tests for handlers/bot_status.py: tracks whether a user has blocked the
bot via Telegram's `my_chat_member` update.
"""

from datetime import datetime, timezone

from aiogram.types import Chat, ChatMemberBanned, ChatMemberLeft, ChatMemberMember, ChatMemberUpdated
from aiogram.types import User as TgUser
from sqlalchemy import select

from database import crud
from database.models import User
from handlers.bot_status import track_bot_blocked_status

BOT_TG_USER = TgUser(id=999999, is_bot=True, first_name="Bot")


async def _fetch_user(session, user_id: int) -> User:
    """Plain read with no side effects — unlike get_or_create_user, which
    (by design, see TestUnblockSafetyNet below) clears is_blocked on any
    call, which would mask exactly what these tests check."""
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one()


def _member_updated(user_id: int, chat_type: str, old_status: str, new_status: str) -> ChatMemberUpdated:
    status_to_member: dict[str, ChatMemberMember | ChatMemberBanned | ChatMemberLeft] = {
        "member": ChatMemberMember(user=BOT_TG_USER),
        "kicked": ChatMemberBanned(user=BOT_TG_USER, until_date=datetime.now(timezone.utc)),
        "left": ChatMemberLeft(user=BOT_TG_USER),
    }
    return ChatMemberUpdated(
        chat=Chat(id=user_id, type=chat_type),
        from_user=TgUser(id=user_id, is_bot=False, first_name="Test", username="tester"),
        date=datetime.now(timezone.utc),
        old_chat_member=status_to_member[old_status],
        new_chat_member=status_to_member[new_status],
    )


class TestBlockDetection:
    async def test_marks_user_blocked_when_kicked_in_private_chat(self, db_session):
        await crud.get_or_create_user(db_session, 100, "tester", "Test User")
        event = _member_updated(100, "private", old_status="member", new_status="kicked")

        await track_bot_blocked_status(event, db_session)
        await db_session.commit()

        refreshed = await _fetch_user(db_session, 100)
        assert refreshed.is_blocked is True
        assert refreshed.blocked_at is not None

    async def test_ignores_status_changes_outside_a_private_chat(self, db_session):
        await crud.get_or_create_user(db_session, 100, "tester", "Test User")
        event = _member_updated(100, "group", old_status="member", new_status="kicked")

        await track_bot_blocked_status(event, db_session)
        await db_session.commit()

        refreshed = await _fetch_user(db_session, 100)
        assert refreshed.is_blocked is False


class TestUnblockDetection:
    async def test_marks_user_unblocked_when_membership_restored(self, db_session):
        await crud.get_or_create_user(db_session, 100, "tester", "Test User")
        await crud.mark_user_blocked(db_session, 100)
        await db_session.commit()

        event = _member_updated(100, "private", old_status="kicked", new_status="member")
        await track_bot_blocked_status(event, db_session)
        await db_session.commit()

        refreshed = await _fetch_user(db_session, 100)
        assert refreshed.is_blocked is False
        assert refreshed.blocked_at is None


class TestUnblockSafetyNet:
    """
    Belt-and-suspenders: if a my_chat_member update was ever missed (e.g.
    the bot was down when the user unblocked it), any further interaction
    that touches get_or_create_user clears the stale flag on its own.
    """

    async def test_get_or_create_user_clears_a_stale_blocked_flag_on_contact(self, db_session):
        await crud.get_or_create_user(db_session, 100, "tester", "Test User")
        await crud.mark_user_blocked(db_session, 100)
        await db_session.commit()

        user = await crud.get_or_create_user(db_session, 100, "tester", "Test User")

        assert user.is_blocked is False
