"""
Tests for handlers/medicines/add.py: the multi-step "add a new medicine"
FSM flow (name -> form -> dosage -> time -> duration -> [timezone] ->
track_stock -> [stock_amount -> stock_threshold] -> saved).
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.medicines import add as add_module
from handlers.medicines.add import (
    add_duration,
    add_form,
    add_name,
    add_start,
    add_stock_amount,
    add_stock_threshold,
    add_time,
    add_timezone,
    add_track_stock,
)
from handlers.medicines.states import AddMedicine


class _FakeState:
    """Minimal FSMContext stand-in backed by a plain dict, so multi-step
    flows can be exercised the way aiogram actually drives them."""

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


def _fake_message(text: str, user_id: int = 1, chat_id: int | None = None):
    message = create_autospec(Message, instance=True)
    message.text = text
    message.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    message.chat = MagicMock(id=chat_id if chat_id is not None else user_id)
    message.answer = AsyncMock()
    return message


def _fake_call(user_id: int, data: str):
    message = create_autospec(Message, instance=True)
    message.edit_text = AsyncMock()
    message.answer = AsyncMock()
    message.chat = MagicMock(id=user_id)
    message.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")

    call = create_autospec(CallbackQuery, instance=True)
    call.data = data
    call.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    call.answer = AsyncMock()
    call.message = message
    return call, message


class TestAddStart:
    async def test_asks_for_name_and_sets_state(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "med_add")
        state = _FakeState()

        await add_start(call, state, db_session)

        message.edit_text.assert_awaited_once()
        assert state.state == AddMedicine.name


class TestAddNameFormDosage:
    async def test_add_name_stores_name_and_advances(self):
        message = _fake_message("Ibuprofen")
        state = _FakeState(data={"lang": "en"})

        await add_name(message, state)

        assert (await state.get_data())["name"] == "Ibuprofen"
        assert state.state == AddMedicine.form
        message.answer.assert_awaited_once()

    async def test_add_name_no_op_without_text(self):
        message = _fake_message(None)
        state = _FakeState(data={"lang": "en"})

        await add_name(message, state)

        assert "name" not in await state.get_data()
        message.answer.assert_not_awaited()

    async def test_add_form_strips_whitespace(self):
        message = _fake_message("  tablets  ")
        state = _FakeState(data={"lang": "en"})

        await add_form(message, state)

        assert (await state.get_data())["form"] == "tablets"
        assert state.state == AddMedicine.dosage

    async def test_add_dosage_advances_to_time(self):
        message = _fake_message("200mg")
        state = _FakeState(data={"lang": "en"})

        await add_dosage_module_call(message, state)

        assert (await state.get_data())["dosage"] == "200mg"
        assert state.state == AddMedicine.time


async def add_dosage_module_call(message, state):
    from handlers.medicines.add import add_dosage

    await add_dosage(message, state)


class TestAddTime:
    async def test_valid_times_advance_to_duration(self):
        message = _fake_message("09:00, 21:00")
        state = _FakeState(data={"lang": "en"})

        await add_time(message, state)

        assert (await state.get_data())["time"] == ["09:00", "21:00"]
        assert state.state == AddMedicine.duration

    async def test_invalid_time_shows_error_and_stays_on_time_state(self):
        message = _fake_message("not-a-time")
        state = _FakeState(data={"lang": "en"})

        await add_time(message, state)

        assert "time" not in await state.get_data()
        assert state.state is None  # never advanced
        message.answer.assert_awaited_once()


class TestAddDuration:
    async def test_invalid_duration_shows_error(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message("not-a-number")
        state = _FakeState(data={"lang": "en"})

        await add_duration(message, state, db_session)

        assert "duration" not in await state.get_data()
        message.answer.assert_awaited_once()

    async def test_zero_duration_is_rejected(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message("0")
        state = _FakeState(data={"lang": "en"})

        await add_duration(message, state, db_session)

        assert "duration" not in await state.get_data()

    async def test_valid_duration_with_existing_timezone_skips_to_track_stock(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        await crud.update_user_timezone(db_session, 1, "Europe/Kyiv")
        message = _fake_message("10")
        state = _FakeState(data={"lang": "en"})

        await add_duration(message, state, db_session)

        data = await state.get_data()
        assert data["duration"] == 10
        assert data["timezone"] == "Europe/Kyiv"
        assert state.state == AddMedicine.track_stock

    async def test_valid_duration_without_timezone_asks_for_it(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message("10")
        state = _FakeState(data={"lang": "en"})

        await add_duration(message, state, db_session)

        assert state.state == AddMedicine.timezone


class TestAddTimezone:
    async def test_unresolvable_place_shows_error(self, db_session, monkeypatch):
        monkeypatch.setattr(add_module, "resolve_timezone_from_place", AsyncMock(return_value=None))
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message("Nowhereville")
        state = _FakeState(data={"lang": "en"})

        await add_timezone(message, state, db_session)

        message.answer.assert_awaited_once()
        assert state.state is None

    async def test_resolved_place_saves_timezone_and_advances(self, db_session, monkeypatch):
        monkeypatch.setattr(add_module, "resolve_timezone_from_place", AsyncMock(return_value="Europe/Kyiv"))
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message("Kyiv")
        state = _FakeState(data={"lang": "en"})

        await add_timezone(message, state, db_session)

        assert (await state.get_data())["timezone"] == "Europe/Kyiv"
        assert state.state == AddMedicine.track_stock
        assert await crud.get_user_timezone(db_session, 1) == "Europe/Kyiv"


class TestAddTrackStock:
    async def test_yes_asks_for_stock_amount(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "track_stock_yes")
        state = _FakeState()

        await add_track_stock(call, state, db_session, MagicMock(), MagicMock())

        message.edit_text.assert_awaited_once()
        assert state.state == AddMedicine.stock_amount

    async def test_no_saves_the_medicine_immediately(self, db_session, monkeypatch):
        monkeypatch.setattr(add_module, "add_reminders_for_medicine", MagicMock())
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "track_stock_no")
        state = _FakeState(
            data={
                "lang": "en",
                "name": "Ibuprofen",
                "form": "tablets",
                "dosage": "200mg",
                "time": ["09:00"],
                "duration": 5,
                "timezone": "Europe/Kyiv",
            }
        )

        await add_track_stock(call, state, db_session, MagicMock(), MagicMock())

        medicines = await crud.get_user_medicines(db_session, 1)
        assert len(medicines) == 1
        assert medicines[0].name == "Ibuprofen"
        add_module.add_reminders_for_medicine.assert_called_once()


class TestAddStockAmountAndThreshold:
    async def test_invalid_amount_shows_error(self):
        message = _fake_message("not-a-number")
        state = _FakeState(data={"lang": "en"})

        await add_stock_amount(message, state)

        assert "stock_amount" not in await state.get_data()

    async def test_valid_amount_advances_to_threshold(self):
        message = _fake_message("30")
        state = _FakeState(data={"lang": "en"})

        await add_stock_amount(message, state)

        assert (await state.get_data())["stock_amount"] == 30
        assert state.state == AddMedicine.stock_threshold

    async def test_invalid_threshold_shows_error(self, db_session):
        message = _fake_message("not-a-number")
        state = _FakeState(data={"lang": "en", "stock_amount": 30})

        await add_stock_threshold(message, state, db_session, MagicMock(), MagicMock())

        assert "stock_threshold" not in await state.get_data()

    async def test_valid_threshold_saves_the_medicine(self, db_session, monkeypatch):
        monkeypatch.setattr(add_module, "add_reminders_for_medicine", MagicMock())
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message("5")
        state = _FakeState(
            data={
                "lang": "en",
                "name": "Ibuprofen",
                "form": "tablets",
                "dosage": "200mg",
                "time": ["09:00"],
                "duration": 5,
                "timezone": "Europe/Kyiv",
                "stock_amount": 30,
            }
        )

        await add_stock_threshold(message, state, db_session, MagicMock(), MagicMock())

        medicines = await crud.get_user_medicines(db_session, 1)
        assert len(medicines) == 1
        assert medicines[0].stock_amount == 30
        assert medicines[0].low_stock_threshold == 5


class TestSaveNewMedicine:
    async def test_computes_total_doses_as_duration_times_slot_count(self, db_session, monkeypatch):
        monkeypatch.setattr(add_module, "add_reminders_for_medicine", MagicMock())
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message("irrelevant")
        state = _FakeState(
            data={
                "lang": "en",
                "name": "Ibuprofen",
                "form": "tablets",
                "dosage": "200mg",
                "time": ["09:00", "21:00"],
                "duration": 5,
                "timezone": "Europe/Kyiv",
            }
        )

        await add_module._save_new_medicine(message, state, db_session, MagicMock(), "en", None, None, MagicMock())

        medicines = await crud.get_user_medicines(db_session, 1)
        # 5 days * 2 doses/day = 10 total doses (course_duration)
        assert medicines[0].course_duration == 10

    async def test_clears_state_after_saving(self, db_session, monkeypatch):
        monkeypatch.setattr(add_module, "add_reminders_for_medicine", MagicMock())
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message("irrelevant")
        state = _FakeState(
            data={
                "lang": "en",
                "name": "Ibuprofen",
                "form": "tablets",
                "dosage": "200mg",
                "time": ["09:00"],
                "duration": 5,
                "timezone": "Europe/Kyiv",
            }
        )

        await add_module._save_new_medicine(message, state, db_session, MagicMock(), "en", None, None, MagicMock())

        assert await state.get_data() == {}
        assert state.state is None
