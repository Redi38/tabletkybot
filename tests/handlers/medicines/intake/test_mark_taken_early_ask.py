"""
Tests for handlers/medicines/intake.py — mark_taken_early_ask: the
confirmation prompt shown before logging an early "taken today" dose from
the medicine settings menu.
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.medicines.intake import mark_taken_early_ask

from ._fixtures import _add_medicine


def _fake_ask_call(user_id: int, medicine_id: int, message_id: int = 1):
    message = create_autospec(Message, instance=True)
    message.message_id = message_id
    message.edit_text = AsyncMock()
    message.answer = AsyncMock()

    call = create_autospec(CallbackQuery, instance=True)
    call.data = f"mark_taken_early_ask_{medicine_id}"
    call.from_user = MagicMock(id=user_id, username="tester")
    call.answer = AsyncMock()
    call.message = message

    return call, message


class TestMarkTakenEarlyAsk:
    async def test_shows_confirmation_without_recording_a_dose(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session, course_duration=10)
        call, message = _fake_ask_call(user_id=1, medicine_id=medicine.id)

        await mark_taken_early_ask(call, db_session)

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.course_duration == 10  # unchanged — nothing recorded yet
        message.edit_text.assert_awaited_once()
        call.answer.assert_awaited_once()

    async def test_confirmation_keyboard_offers_yes_and_no(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session, course_duration=10)
        call, message = _fake_ask_call(user_id=1, medicine_id=medicine.id)

        await mark_taken_early_ask(call, db_session)

        kb = message.edit_text.await_args.kwargs["reply_markup"]
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert f"mark_taken_early_{medicine.id}" in callbacks
        assert f"edit_med_{medicine.id}" in callbacks
