"""
Tests for handlers/medicines/extend.py: extending an active medicine's
course, or restoring an archived one, by asking for a number of days and
rescheduling reminders accordingly.
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.medicines.extend import extend_course_save, extend_course_start
from handlers.medicines.states import ExtendMedicine


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


async def _add_medicine(db_session, user_id=1, schedule_times=("09:00",), is_active=True):
    await crud.get_or_create_user(db_session, user_id, "tester", "Test User")
    medicine = await crud.add_medicine(
        db_session,
        user_id=user_id,
        name="Ibuprofen",
        form="tablets",
        dosage="200mg",
        schedules_list=list(schedule_times),
        course_duration=5,
    )
    if not is_active:
        await crud.update_medicine_field(db_session, medicine.id, "is_active", False)
    await db_session.commit()
    return medicine


class TestExtendCourseStart:
    async def test_restore_ask_sets_state_and_medicine_id(self, db_session):
        medicine = await _add_medicine(db_session, is_active=False)
        call, message = _fake_call(1, f"med_restore_ask_{medicine.id}")
        state = _FakeState()

        await extend_course_start(call, state, db_session)

        message.edit_text.assert_awaited_once()
        assert state.state == ExtendMedicine.waiting_for_days
        assert (await state.get_data())["medicine_id"] == medicine.id

    async def test_extend_ask_sets_state_too(self, db_session):
        medicine = await _add_medicine(db_session, is_active=True)
        call, message = _fake_call(1, f"med_extend_ask_{medicine.id}")
        state = _FakeState()

        await extend_course_start(call, state, db_session)

        assert state.state == ExtendMedicine.waiting_for_days

    async def test_no_op_when_medicine_missing(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "med_restore_ask_999")
        state = _FakeState()

        await extend_course_start(call, state, db_session)

        message.edit_text.assert_not_awaited()


class TestExtendCourseSave:
    async def test_invalid_days_shows_error_without_saving(self, db_session, mock_redis, mock_bot):
        medicine = await _add_medicine(db_session, is_active=False)
        message = _fake_message("not-a-number")
        state = _FakeState(data={"lang": "en", "medicine_id": medicine.id})

        await extend_course_save(message, state, db_session, mock_bot, MagicMock())

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.is_active is False  # unchanged
        message.answer.assert_awaited_once()

    async def test_reactivates_an_archived_medicine(self, db_session, mock_redis, mock_bot):
        medicine = await _add_medicine(db_session, is_active=False)
        message = _fake_message("10")
        state = _FakeState(data={"lang": "en", "medicine_id": medicine.id})

        await extend_course_save(message, state, db_session, mock_bot, MagicMock())

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.is_active is True

    async def test_course_duration_is_scaled_by_slot_count(self, db_session, mock_redis, mock_bot):
        medicine = await _add_medicine(db_session, schedule_times=("09:00", "21:00"), is_active=True)
        message = _fake_message("7", user_id=1)  # 7 days
        state = _FakeState(data={"lang": "en", "medicine_id": medicine.id})

        await extend_course_save(message, state, db_session, mock_bot, MagicMock())

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.course_duration == 14  # 7 days * 2 doses/day

    async def test_shows_mark_taken_and_list_buttons(self, db_session, mock_redis, mock_bot):
        medicine = await _add_medicine(db_session, is_active=False)
        message = _fake_message("10")
        state = _FakeState(data={"lang": "en", "medicine_id": medicine.id})

        await extend_course_save(message, state, db_session, mock_bot, MagicMock())

        message.answer.assert_awaited_once()
        keyboard = message.answer.call_args.kwargs["reply_markup"]
        callback_data = {btn.callback_data for row in keyboard.inline_keyboard for btn in row}
        assert f"mark_taken_now_{medicine.id}" in callback_data
        assert "med_list" in callback_data

    async def test_reschedules_reminders_after_saving(self, db_session, mock_redis, mock_bot, monkeypatch):
        import handlers.medicines.extend as extend_module

        monkeypatch.setattr(extend_module, "add_reminders_for_medicine", MagicMock())
        medicine = await _add_medicine(db_session, is_active=False)
        message = _fake_message("10")
        state = _FakeState(data={"lang": "en", "medicine_id": medicine.id})

        await extend_course_save(message, state, db_session, mock_bot, MagicMock())

        extend_module.add_reminders_for_medicine.assert_called_once()

    async def test_clears_state_after_saving(self, db_session, mock_redis, mock_bot):
        medicine = await _add_medicine(db_session, is_active=False)
        message = _fake_message("10")
        state = _FakeState(data={"lang": "en", "medicine_id": medicine.id})

        await extend_course_save(message, state, db_session, mock_bot, MagicMock())

        assert await state.get_data() == {}
        assert state.state is None

    async def test_no_op_when_medicine_missing(self, db_session, mock_redis, mock_bot):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        message = _fake_message("10")
        state = _FakeState(data={"lang": "en", "medicine_id": 999})

        await extend_course_save(message, state, db_session, mock_bot, MagicMock())

        message.answer.assert_not_awaited()
