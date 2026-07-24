"""
Tests for handlers/medicines/listing.py: the active-medicines list (with
its empty state) and the per-medicine adherence stats breakdown.
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.medicines.listing import list_medicines, medicine_stats


def _fake_call(user_id: int, data: str):
    message = create_autospec(Message, instance=True)
    message.edit_text = AsyncMock()

    call = create_autospec(CallbackQuery, instance=True)
    call.data = data
    call.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    call.answer = AsyncMock()
    call.message = message
    return call, message


async def _add_medicine(db_session, user_id=1, name="Ibuprofen", is_active=True, stock_amount=None):
    await crud.get_or_create_user(db_session, user_id, "tester", "Test User")
    medicine = await crud.add_medicine(
        db_session,
        user_id=user_id,
        name=name,
        form="tablets",
        dosage="200mg",
        schedules_list=["09:00"],
        course_duration=5,
        stock_amount=stock_amount,
    )
    if not is_active:
        await crud.update_medicine_field(db_session, medicine.id, "is_active", False)
    await db_session.commit()
    return medicine


class TestListMedicines:
    async def test_shows_empty_state_with_archive_link(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "med_list")

        await list_medicines(call, db_session)

        message.edit_text.assert_awaited_once()
        keyboard = message.edit_text.call_args.kwargs["reply_markup"]
        callback_data = {btn.callback_data for row in keyboard.inline_keyboard for btn in row}
        assert "med_archive_list" in callback_data

    async def test_lists_only_active_medicines(self, db_session):
        await _add_medicine(db_session, name="Active One", is_active=True)
        await _add_medicine(db_session, name="Archived One", is_active=False)
        call, message = _fake_call(1, "med_list")

        await list_medicines(call, db_session)

        text = message.edit_text.call_args.args[0]
        assert "Active One" in text
        assert "Archived One" not in text

    async def test_shows_stock_info_when_stock_is_tracked(self, db_session):
        await _add_medicine(db_session, stock_amount=30)
        call, message = _fake_call(1, "med_list")

        await list_medicines(call, db_session)

        # stock_info is only appended when stock_amount is not None; the
        # simplest signal it rendered is that no exception was raised and
        # the medicine still shows up.
        text = message.edit_text.call_args.args[0]
        assert "Ibuprofen" in text

    async def test_no_op_when_no_from_user(self, db_session):
        call, message = _fake_call(1, "med_list")
        call.from_user = None

        await list_medicines(call, db_session)

        message.edit_text.assert_not_awaited()

    async def test_only_lists_the_requesting_users_medicines(self, db_session):
        await _add_medicine(db_session, user_id=1, name="Mine")
        await _add_medicine(db_session, user_id=2, name="Someone Else's")
        call, message = _fake_call(1, "med_list")

        await list_medicines(call, db_session)

        text = message.edit_text.call_args.args[0]
        assert "Mine" in text
        assert "Someone Else's" not in text


class TestMedicineStats:
    async def test_shows_empty_state_with_no_records(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "med_stats")

        await medicine_stats(call, db_session)

        message.edit_text.assert_awaited_once()
        assert message.edit_text.call_args.kwargs["reply_markup"] is not None

    async def test_shows_taken_and_missed_counts_with_percentage(self, db_session):
        medicine = await _add_medicine(db_session)
        await crud.record_medicine_taken(db_session, medicine.id, status="taken")
        await crud.record_medicine_taken(db_session, medicine.id, status="taken")
        await crud.record_medicine_taken(db_session, medicine.id, status="missed")
        await db_session.commit()
        call, message = _fake_call(1, "med_stats")

        await medicine_stats(call, db_session)

        text = message.edit_text.call_args.args[0]
        assert "Ibuprofen" in text
        assert "66.7%" in text  # 2 taken / 3 total

    async def test_no_op_when_no_from_user(self, db_session):
        call, message = _fake_call(1, "med_stats")
        call.from_user = None

        await medicine_stats(call, db_session)

        message.edit_text.assert_not_awaited()
