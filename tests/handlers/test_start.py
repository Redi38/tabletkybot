"""
Tests for handlers/start.py: /start, /help.
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.types import Message

from database import crud
from handlers.start import cmd_start


def _fake_message(user_id: int, text: str = "/start"):
    message = create_autospec(Message, instance=True)
    message.text = text
    message.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    message.answer = AsyncMock()
    return message


def _fake_state():
    state = MagicMock()
    state.clear = AsyncMock()
    return state


class TestCmdStart:
    async def test_sends_greeting_with_main_keyboard(self, db_session):
        message = _fake_message(1)
        state = _fake_state()

        await cmd_start(message, db_session, state)

        message.answer.assert_awaited_once()
        assert message.answer.call_args.kwargs["reply_markup"] is not None

    async def test_creates_user_on_first_start(self, db_session):
        message = _fake_message(42)
        state = _fake_state()

        await cmd_start(message, db_session, state)

        user = await crud.get_or_create_user(db_session, 42, "tester", "Test User")
        assert user is not None

    async def test_clears_fsm_state(self, db_session):
        message = _fake_message(1)
        state = _fake_state()

        await cmd_start(message, db_session, state)

        state.clear.assert_awaited_once()
