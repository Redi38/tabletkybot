"""
Tests for handlers/medicines/restock.py: asking for a restock amount
(from either "restock and take now" or "restock only"), skipping the
current dose, and saving the entered amount.
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.medicines.restock import restock_ask_amount, restock_save, restock_skip
from handlers.medicines.states import RestockMedicine


class _FakeState:
    def __init__(self, data=None):
        self._data = dict(data or {})
        self.state = None

    async def update_data(self, **kwargs):
        self._data.update(kwargs)

    async def get_data(self):
        return dict(self._data)

    async def set_state(self, state):
        self.state = state

    async def clear(self):
        self._data = {}
        self.state = None


def _fake_message(text: str | None, user_id: int = 1):
    message = create_autospec(Message, instance=True)
    message.text = text
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


async def _add_medicine(db_session, user_id=1, stock_amount=0):
    await crud.get_or_create_user(db_session, user_id, "tester", "Test User")
    medicine = await crud.add_medicine(
        db_session,
        user_id=user_id,
        name="Ibuprofen",
        form="tablets",
        dosage="200mg",
        schedules_list=["09:00"],
        course_duration=5,
        stock_amount=stock_amount,
    )
    await db_session.commit()
    return medicine


class TestRestockAskAmount:
    async def test_restock_only_sets_needs_take_false(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session)
        call, message = _fake_call(1, f"restock_ask_{medicine.id}")
        state = _FakeState()

        await restock_ask_amount(call, state, db_session)

        message.edit_text.assert_awaited_once()
        assert state.state == RestockMedicine.waiting_for_amount
        data = await state.get_data()
        assert data["needs_take"] is False
        assert data["medicine_id"] == medicine.id

    async def test_restock_and_take_sets_needs_take_true(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session)
        call, message = _fake_call(1, f"restock_yes_{medicine.id}")
        state = _FakeState()

        await restock_ask_amount(call, state, db_session)

        data = await state.get_data()
        assert data["needs_take"] is True

    async def test_no_op_when_medicine_missing(self, db_session, mock_redis):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "restock_ask_999")
        state = _FakeState()

        await restock_ask_amount(call, state, db_session)

        message.edit_text.assert_not_awaited()


class TestRestockSkip:
    async def test_records_a_skipped_dose(self, db_session):
        medicine = await _add_medicine(db_session)
        call, message = _fake_call(1, f"restock_no_{medicine.id}")

        await restock_skip(call, db_session)

        message.edit_text.assert_awaited_once()

    async def test_no_op_when_medicine_missing(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "restock_no_999")

        await restock_skip(call, db_session)

        message.edit_text.assert_not_awaited()


class TestRestockSave:
    async def test_invalid_amount_shows_error(self, db_session):
        medicine = await _add_medicine(db_session, stock_amount=0)
        message = _fake_message("not-a-number")
        state = _FakeState(data={"lang": "en", "medicine_id": medicine.id, "needs_take": False})

        await restock_save(message, state, db_session)

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.stock_amount == 0
        message.answer.assert_awaited_once()

    async def test_restock_only_adds_to_stock_without_recording_a_dose(self, db_session):
        medicine = await _add_medicine(db_session, stock_amount=0)
        message = _fake_message("30")
        state = _FakeState(data={"lang": "en", "medicine_id": medicine.id, "needs_take": False})

        await restock_save(message, state, db_session)

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.stock_amount == 30
        records = await crud.get_medicine_records_for_report(db_session, 1)
        assert records == []

    async def test_restock_and_take_records_a_dose_and_reflects_final_stock(self, db_session):
        medicine = await _add_medicine(db_session, stock_amount=0)
        message = _fake_message("30")
        state = _FakeState(data={"lang": "en", "medicine_id": medicine.id, "needs_take": True})

        await restock_save(message, state, db_session)

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.stock_amount == 29  # +30 restocked, -1 taken
        records = await crud.get_medicine_records_for_report(db_session, 1)
        assert len(records) == 1
        assert records[0][4] == "taken"  # (name, dosage, remaining_days, taken_at, status)

    async def test_clears_state_after_saving(self, db_session):
        medicine = await _add_medicine(db_session, stock_amount=0)
        message = _fake_message("30")
        state = _FakeState(data={"lang": "en", "medicine_id": medicine.id, "needs_take": False})

        await restock_save(message, state, db_session)

        assert await state.get_data() == {}
        assert state.state is None
