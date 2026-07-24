"""
Tests for handlers/medicines/edit.py: the "what to edit" menu and saving a
new value for name/dosage/schedules/course_duration/stock_amount/
low_stock_threshold, including the reminder-rescheduling side effect.
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.medicines.edit import edit_field_save, edit_field_start, edit_medicine_menu
from handlers.medicines.states import EditMedicine


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


async def _add_medicine(db_session, user_id=1, schedule_times=("09:00",), stock_amount=None):
    await crud.get_or_create_user(db_session, user_id, "tester", "Test User")
    medicine = await crud.add_medicine(
        db_session,
        user_id=user_id,
        name="Ibuprofen",
        form="tablets",
        dosage="200mg",
        schedules_list=list(schedule_times),
        course_duration=5,
        stock_amount=stock_amount,
    )
    await db_session.commit()
    return medicine


class TestEditMedicineMenu:
    async def test_shows_stock_fields_when_stock_is_tracked(self, db_session):
        medicine = await _add_medicine(db_session, stock_amount=30)
        call, message = _fake_call(1, f"edit_med_{medicine.id}")

        await edit_medicine_menu(call, db_session)

        message.edit_text.assert_awaited_once()
        keyboard = message.edit_text.call_args.kwargs["reply_markup"]
        callback_data = {btn.callback_data for row in keyboard.inline_keyboard for btn in row}
        assert f"edit_field_stock_amount_{medicine.id}" in callback_data
        assert f"edit_field_low_stock_threshold_{medicine.id}" in callback_data

    async def test_offers_enable_stock_when_stock_is_not_tracked(self, db_session):
        medicine = await _add_medicine(db_session, stock_amount=None)
        call, message = _fake_call(1, f"edit_med_{medicine.id}")

        await edit_medicine_menu(call, db_session)

        keyboard = message.edit_text.call_args.kwargs["reply_markup"]
        callback_data = {btn.callback_data for row in keyboard.inline_keyboard for btn in row}
        assert f"edit_field_stock_amount_{medicine.id}" in callback_data
        assert f"edit_field_low_stock_threshold_{medicine.id}" not in callback_data

    async def test_no_op_when_medicine_missing(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "edit_med_999")

        await edit_medicine_menu(call, db_session)

        message.edit_text.assert_not_awaited()


class TestEditFieldStart:
    async def test_extracts_the_field_name_and_sets_state(self, db_session):
        medicine = await _add_medicine(db_session)
        call, message = _fake_call(1, f"edit_field_dosage_{medicine.id}")
        state = _FakeState()

        await edit_field_start(call, state, db_session)

        message.edit_text.assert_awaited_once()
        assert state.state == EditMedicine.waiting_value
        data = await state.get_data()
        assert data["field"] == "dosage"
        assert data["medicine_id"] == medicine.id

    async def test_extracts_multi_word_field_name(self, db_session):
        medicine = await _add_medicine(db_session)
        call, _ = _fake_call(1, f"edit_field_low_stock_threshold_{medicine.id}")
        state = _FakeState()

        await edit_field_start(call, state, db_session)

        assert (await state.get_data())["field"] == "low_stock_threshold"


class TestEditFieldSave:
    async def test_saves_a_simple_text_field(self, db_session, mock_redis, mock_bot):
        medicine = await _add_medicine(db_session)
        message = _fake_message("500mg", user_id=1)
        state = _FakeState(data={"medicine_id": medicine.id, "field": "dosage", "lang": "en"})

        await edit_field_save(message, state, db_session, mock_bot, MagicMock())

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.dosage == "500mg"
        assert await state.get_data() == {}

    async def test_invalid_schedules_shows_error_without_saving(self, db_session, mock_redis, mock_bot):
        medicine = await _add_medicine(db_session, schedule_times=("09:00",))
        message = _fake_message("not-a-time", user_id=1)
        state = _FakeState(data={"medicine_id": medicine.id, "field": "schedules", "lang": "en"})

        await edit_field_save(message, state, db_session, mock_bot, MagicMock())

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert [s.scheduled_time for s in refreshed.schedules] == ["09:00"]
        message.answer.assert_awaited_once()

    async def test_valid_schedules_rescales_course_duration_proportionally(self, db_session, mock_redis, mock_bot):
        # 5-day course at 1x/day (course_duration=5) -> switching to 2x/day
        # should scale course_duration to 10 to keep the same number of days.
        medicine = await _add_medicine(db_session, schedule_times=("09:00",))
        message = _fake_message("09:00, 21:00", user_id=1)
        state = _FakeState(data={"medicine_id": medicine.id, "field": "schedules", "lang": "en"})

        await edit_field_save(message, state, db_session, mock_bot, MagicMock())

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert len(refreshed.schedules) == 2
        assert refreshed.course_duration == 10

    async def test_invalid_course_duration_shows_error(self, db_session, mock_redis, mock_bot):
        medicine = await _add_medicine(db_session)
        message = _fake_message("not-a-number", user_id=1)
        state = _FakeState(data={"medicine_id": medicine.id, "field": "course_duration", "lang": "en"})

        await edit_field_save(message, state, db_session, mock_bot, MagicMock())

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.course_duration == 5  # unchanged
        message.answer.assert_awaited_once()

    async def test_course_duration_is_scaled_by_slot_count(self, db_session, mock_redis, mock_bot):
        medicine = await _add_medicine(db_session, schedule_times=("09:00", "21:00"))
        message = _fake_message("7", user_id=1)  # 7 days
        state = _FakeState(data={"medicine_id": medicine.id, "field": "course_duration", "lang": "en"})

        await edit_field_save(message, state, db_session, mock_bot, MagicMock())

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.course_duration == 14  # 7 days * 2 doses/day

    async def test_invalid_stock_amount_shows_error(self, db_session, mock_redis, mock_bot):
        medicine = await _add_medicine(db_session, stock_amount=30)
        message = _fake_message("not-a-number", user_id=1)
        state = _FakeState(data={"medicine_id": medicine.id, "field": "stock_amount", "lang": "en"})

        await edit_field_save(message, state, db_session, mock_bot, MagicMock())

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.stock_amount == 30  # unchanged

    async def test_valid_stock_amount_saves_directly_without_scaling(self, db_session, mock_redis, mock_bot):
        medicine = await _add_medicine(db_session, stock_amount=30)
        message = _fake_message("50", user_id=1)
        state = _FakeState(data={"medicine_id": medicine.id, "field": "stock_amount", "lang": "en"})

        await edit_field_save(message, state, db_session, mock_bot, MagicMock())

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.stock_amount == 50

    async def test_reschedules_reminders_after_saving(self, db_session, mock_redis, mock_bot, monkeypatch):
        import handlers.medicines.edit as edit_module

        monkeypatch.setattr(edit_module, "add_reminders_for_medicine", MagicMock())
        medicine = await _add_medicine(db_session)
        message = _fake_message("500mg", user_id=1)
        state = _FakeState(data={"medicine_id": medicine.id, "field": "dosage", "lang": "en"})

        await edit_field_save(message, state, db_session, mock_bot, MagicMock())

        edit_module.add_reminders_for_medicine.assert_called_once()
