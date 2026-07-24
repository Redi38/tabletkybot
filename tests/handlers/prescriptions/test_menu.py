"""
Tests for handlers/prescriptions/menu.py: top-level prescriptions menu
navigation (open menu, back to menu, back to main menu).
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.prescriptions.menu import back_to_main_menu, back_to_presc_menu, prescriptions_menu


def _fake_message(user_id: int):
    message = create_autospec(Message, instance=True)
    message.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    message.answer = AsyncMock()
    return message


def _fake_call(user_id: int, data: str):
    message = create_autospec(Message, instance=True)
    message.edit_text = AsyncMock()

    call = create_autospec(CallbackQuery, instance=True)
    call.data = data
    call.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    call.answer = AsyncMock()
    call.message = message
    return call, message


def _fake_state():
    state = create_autospec(FSMContext, instance=True)
    state.clear = AsyncMock()
    return state


class TestPrescriptionsMenu:
    async def test_shows_the_menu_with_keyboard(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message(1)

        await prescriptions_menu(message, db_session)

        message.answer.assert_awaited_once()
        assert message.answer.call_args.kwargs["reply_markup"] is not None

    async def test_no_op_when_no_from_user(self, db_session):
        message = _fake_message(1)
        message.from_user = None

        await prescriptions_menu(message, db_session)

        message.answer.assert_not_awaited()


class TestBackToPrescMenu:
    async def test_edits_message_and_clears_state(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "presc_menu")
        state = _fake_state()

        await back_to_presc_menu(call, state, db_session)

        state.clear.assert_awaited_once()
        message.edit_text.assert_awaited_once()
        call.answer.assert_awaited_once()


class TestBackToMainMenu:
    async def test_shows_the_start_text_with_the_users_name(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Custom Name")
        call, message = _fake_call(1, "presc_back")
        state = _fake_state()

        await back_to_main_menu(call, state, db_session)

        state.clear.assert_awaited_once()
        message.edit_text.assert_awaited_once()
        assert "Custom Name" in message.edit_text.call_args.args[0]
