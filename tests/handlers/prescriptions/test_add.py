"""
Tests for handlers/prescriptions/add.py: the multi-step "add a new
prescription" FSM flow (name -> valid_from -> duration -> quantity ->
reminder -> saved).
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.prescriptions.add import (
    add_name,
    add_quantity,
    add_reminder,
    add_start,
    add_valid_from,
    duration_chosen,
)
from handlers.prescriptions.states import AddPrescription


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


class TestAddStart:
    async def test_asks_for_name_and_sets_state(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "presc_add")
        state = _FakeState()

        await add_start(call, state, db_session)

        message.edit_text.assert_awaited_once()
        assert state.state == AddPrescription.name


class TestAddName:
    async def test_stores_name_and_advances(self):
        message = _fake_message("Amoxicillin")
        state = _FakeState(data={"lang": "en"})

        await add_name(message, state)

        assert (await state.get_data())["name"] == "Amoxicillin"
        assert state.state == AddPrescription.valid_from

    async def test_no_op_without_text(self):
        message = _fake_message(None)
        state = _FakeState(data={"lang": "en"})

        await add_name(message, state)

        assert "name" not in await state.get_data()
        message.answer.assert_not_awaited()


class TestAddValidFrom:
    async def test_invalid_date_shows_error_and_stays(self):
        message = _fake_message("not-a-date")
        state = _FakeState(data={"lang": "en"})

        await add_valid_from(message, state)

        assert "valid_from" not in await state.get_data()
        assert state.state is None

    async def test_valid_date_shows_duration_choice(self):
        message = _fake_message("01.01.2026")
        state = _FakeState(data={"lang": "en"})

        await add_valid_from(message, state)

        data = await state.get_data()
        assert data["valid_from"] == "2026-01-01"
        assert state.state == AddPrescription.duration
        assert message.answer.call_args.kwargs["reply_markup"] is not None


class TestDurationChosen:
    async def test_30_days_computes_expiry_and_asks_for_quantity(self):
        call, message = _fake_call(1, "presc_dur_30")
        state = _FakeState(data={"lang": "en", "valid_from": "2026-01-01"})

        await duration_chosen(call, state)

        data = await state.get_data()
        assert data["expires"] == "2026-01-31"
        assert state.state == AddPrescription.quantity
        message.edit_text.assert_awaited_once()

    async def test_60_days_computes_expiry(self):
        call, message = _fake_call(1, "presc_dur_60")
        state = _FakeState(data={"lang": "en", "valid_from": "2026-01-01"})

        await duration_chosen(call, state)

        data = await state.get_data()
        assert data["expires"] == "2026-03-02"


class TestAddQuantity:
    async def test_dash_skips_quantity(self):
        message = _fake_message("-")
        state = _FakeState(data={"lang": "en"})

        await add_quantity(message, state)

        assert (await state.get_data())["quantity"] is None
        assert state.state == AddPrescription.reminder

    async def test_valid_quantity_advances(self):
        message = _fake_message("30")
        state = _FakeState(data={"lang": "en"})

        await add_quantity(message, state)

        assert (await state.get_data())["quantity"] == 30
        assert state.state == AddPrescription.reminder

    async def test_invalid_quantity_shows_error_and_stays(self):
        message = _fake_message("not-a-number")
        state = _FakeState(data={"lang": "en"})

        await add_quantity(message, state)

        assert "quantity" not in await state.get_data()
        assert state.state is None


class TestAddReminder:
    async def test_saves_the_prescription_with_default_reminder_days(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message("-", user_id=1)
        state = _FakeState(
            data={
                "lang": "en",
                "name": "Amoxicillin",
                "valid_from": "2026-01-01",
                "expires": "2026-01-31",
                "quantity": 30,
            }
        )

        await add_reminder(message, state, db_session)

        prescriptions = await crud.get_user_prescriptions(db_session, 1)
        assert len(prescriptions) == 1
        assert prescriptions[0].medicine_name == "Amoxicillin"
        assert prescriptions[0].reminder_days_before == 3  # default
        assert prescriptions[0].max_quantity == 30

    async def test_saves_with_custom_reminder_days(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message("7", user_id=1)
        state = _FakeState(
            data={
                "lang": "en",
                "name": "Amoxicillin",
                "valid_from": "2026-01-01",
                "expires": "2026-01-31",
                "quantity": None,
            }
        )

        await add_reminder(message, state, db_session)

        prescriptions = await crud.get_user_prescriptions(db_session, 1)
        assert prescriptions[0].reminder_days_before == 7

    async def test_invalid_reminder_days_shows_error_without_saving(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message("not-a-number", user_id=1)
        state = _FakeState(
            data={
                "lang": "en",
                "name": "Amoxicillin",
                "valid_from": "2026-01-01",
                "expires": "2026-01-31",
                "quantity": None,
            }
        )

        await add_reminder(message, state, db_session)

        assert await crud.get_user_prescriptions(db_session, 1) == []
        message.answer.assert_awaited_once()

    async def test_clears_state_after_saving(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message("-", user_id=1)
        state = _FakeState(
            data={
                "lang": "en",
                "name": "Amoxicillin",
                "valid_from": "2026-01-01",
                "expires": "2026-01-31",
                "quantity": None,
            }
        )

        await add_reminder(message, state, db_session)

        assert await state.get_data() == {}
        assert state.state is None
