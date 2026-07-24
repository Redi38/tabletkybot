"""
Tests for handlers/prescriptions/listing.py: the active-prescriptions
list, its empty state, and the conditional "mark bought" button that's
hidden once a prescription is fully purchased.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.prescriptions.listing import list_prescriptions


def _fake_call(user_id: int, data: str):
    message = create_autospec(Message, instance=True)
    message.edit_text = AsyncMock()

    call = create_autospec(CallbackQuery, instance=True)
    call.data = data
    call.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    call.answer = AsyncMock()
    call.message = message
    return call, message


async def _add_prescription(db_session, user_id=1, name="Amoxicillin", max_quantity=None):
    await crud.get_or_create_user(db_session, user_id, "tester", "Test User")
    prescription = await crud.add_prescription(
        db_session,
        user_id=user_id,
        medicine_name=name,
        valid_from=date(2026, 1, 1),
        expires_at=date(2026, 12, 31),
        max_quantity=max_quantity,
    )
    await db_session.commit()
    return prescription


def _all_buttons(keyboard):
    return [btn for row in keyboard.inline_keyboard for btn in row]


class TestListPrescriptions:
    async def test_shows_empty_state_with_archive_link(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "presc_list")

        await list_prescriptions(call, db_session)

        message.edit_text.assert_awaited_once()
        keyboard = message.edit_text.call_args.kwargs["reply_markup"]
        callback_data = {btn.callback_data for btn in _all_buttons(keyboard)}
        assert "presc_archive_list" in callback_data
        assert "presc_menu" in callback_data

    async def test_lists_each_prescription_with_edit_and_archive_buttons(self, db_session):
        prescription = await _add_prescription(db_session)
        call, message = _fake_call(1, "presc_list")

        await list_prescriptions(call, db_session)

        text = message.edit_text.call_args.args[0]
        assert prescription.medicine_name in text
        keyboard = message.edit_text.call_args.kwargs["reply_markup"]
        callback_data = {btn.callback_data for btn in _all_buttons(keyboard)}
        assert f"presc_edit_{prescription.id}" in callback_data
        assert f"presc_archive_ask_{prescription.id}" in callback_data

    async def test_shows_mark_bought_button_when_not_fully_purchased(self, db_session):
        prescription = await _add_prescription(db_session, max_quantity=30)
        call, message = _fake_call(1, "presc_list")

        await list_prescriptions(call, db_session)

        keyboard = message.edit_text.call_args.kwargs["reply_markup"]
        callback_data = {btn.callback_data for btn in _all_buttons(keyboard)}
        assert f"presc_buy_ask_{prescription.id}" in callback_data

    async def test_hides_mark_bought_button_once_fully_purchased(self, db_session):
        prescription = await _add_prescription(db_session, max_quantity=10)
        await crud.mark_prescription_purchased(db_session, prescription.id, 10)
        await db_session.commit()
        call, message = _fake_call(1, "presc_list")

        await list_prescriptions(call, db_session)

        keyboard = message.edit_text.call_args.kwargs["reply_markup"]
        callback_data = {btn.callback_data for btn in _all_buttons(keyboard)}
        assert f"presc_buy_ask_{prescription.id}" not in callback_data
        # edit/archive buttons stay available regardless
        assert f"presc_edit_{prescription.id}" in callback_data

    async def test_shows_infinity_symbol_when_no_quantity_limit(self, db_session):
        await _add_prescription(db_session, max_quantity=None)
        call, message = _fake_call(1, "presc_list")

        await list_prescriptions(call, db_session)

        text = message.edit_text.call_args.args[0]
        assert "∞" in text

    async def test_only_lists_the_requesting_users_prescriptions(self, db_session):
        await _add_prescription(db_session, user_id=1, name="Mine")
        await _add_prescription(db_session, user_id=2, name="Someone Else's")
        call, message = _fake_call(1, "presc_list")

        await list_prescriptions(call, db_session)

        text = message.edit_text.call_args.args[0]
        assert "Mine" in text
        assert "Someone Else's" not in text

    async def test_no_op_when_no_from_user(self, db_session):
        call, message = _fake_call(1, "presc_list")
        call.from_user = None

        await list_prescriptions(call, db_session)

        message.edit_text.assert_not_awaited()
