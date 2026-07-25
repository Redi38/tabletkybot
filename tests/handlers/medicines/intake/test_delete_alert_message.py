"""Tests for handlers/medicines/intake.py — delete_alert_message."""

from unittest.mock import AsyncMock, create_autospec

from aiogram.types import CallbackQuery, Message

from handlers.medicines.intake import delete_alert_message


class TestDeleteAlertMessage:
    async def test_deletes_the_message(self):
        message = create_autospec(Message, instance=True)
        message.delete = AsyncMock()
        call = create_autospec(CallbackQuery, instance=True)
        call.data = "delete_message"
        call.message = message

        await delete_alert_message(call)

        message.delete.assert_awaited_once()

    async def test_noop_when_call_message_is_not_a_message(self):
        call = create_autospec(CallbackQuery, instance=True)
        call.data = "delete_message"
        call.message = None  # e.g. an InaccessibleMessage

        await delete_alert_message(call)  # should not raise
