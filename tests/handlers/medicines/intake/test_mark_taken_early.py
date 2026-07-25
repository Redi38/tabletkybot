"""
Tests for handlers/medicines/intake.py — mark_taken_early: logging a dose
taken ahead of a still-pending reminder later today.
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.medicines.intake import mark_taken_early

from ._fixtures import _add_medicine, _fake_state


def _fake_early_call(user_id: int, medicine_id: int, message_id: int = 1):
    message = create_autospec(Message, instance=True)
    message.message_id = message_id
    message.edit_text = AsyncMock()
    message.answer = AsyncMock()

    call = create_autospec(CallbackQuery, instance=True)
    call.data = f"mark_taken_early_{medicine_id}"
    call.from_user = MagicMock(id=user_id, username="tester")
    call.answer = AsyncMock()
    call.message = message

    return call, message


class TestMarkTakenEarlyHappyPath:
    async def test_records_a_taken_dose_same_as_the_missed_flow(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session, course_duration=10)
        call, message = _fake_early_call(user_id=1, medicine_id=medicine.id)
        state = _fake_state()

        await mark_taken_early(call, state, db_session)

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.course_duration == 9
        message.edit_text.assert_awaited_once()


class TestMarkTakenEarlySuppressesTheUpcomingScheduleOnly:
    """
    Regression coverage: logging an early dose must suppress the one
    still-upcoming schedule for today, and must never touch a schedule
    that has already passed today (that's a different dose, still owed).
    """

    async def test_suppresses_the_next_upcoming_schedule_today(self, db_session, mock_redis, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from services.scheduler import _manual_reminder_today
        from services.scheduler.jobs import core as scheduler_jobs_core_module

        fixed_now = datetime(2026, 7, 21, 12, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
        monkeypatch.setattr(
            scheduler_jobs_core_module,
            "datetime",
            type("_DT", (), {"now": staticmethod(lambda tz=None: fixed_now)}),
        )

        # Took the 15:00 dose early, at 12:00 — the 09:00 dose already
        # happened via its own reminder earlier and is unrelated.
        medicine = await _add_medicine(db_session, course_duration=10, schedules_list=["09:00", "15:00"])
        upcoming_schedule_id = next(s.id for s in medicine.schedules if s.scheduled_time == "15:00")
        call, message = _fake_early_call(user_id=1, medicine_id=medicine.id)
        state = _fake_state()

        await mark_taken_early(call, state, db_session)

        assert (medicine.id, upcoming_schedule_id) in _manual_reminder_today

    async def test_does_not_suppress_a_schedule_that_already_passed_today(self, db_session, mock_redis, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from services.scheduler import _manual_reminder_today
        from services.scheduler.jobs import core as scheduler_jobs_core_module

        fixed_now = datetime(2026, 7, 21, 12, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
        monkeypatch.setattr(
            scheduler_jobs_core_module,
            "datetime",
            type("_DT", (), {"now": staticmethod(lambda tz=None: fixed_now)}),
        )

        medicine = await _add_medicine(db_session, course_duration=10, schedules_list=["09:00", "15:00"])
        passed_schedule_id = next(s.id for s in medicine.schedules if s.scheduled_time == "09:00")
        call, message = _fake_early_call(user_id=1, medicine_id=medicine.id)
        state = _fake_state()

        await mark_taken_early(call, state, db_session)

        # The already-passed 09:00 slot is a separate, still-owed dose —
        # marking the early 15:00 dose must never suppress it.
        assert (medicine.id, passed_schedule_id) not in _manual_reminder_today

    async def test_no_suppression_recorded_when_every_schedule_today_has_passed(
        self, db_session, mock_redis, monkeypatch
    ):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from services.scheduler import _manual_reminder_today
        from services.scheduler.jobs import core as scheduler_jobs_core_module

        fixed_now = datetime(2026, 7, 21, 22, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
        monkeypatch.setattr(
            scheduler_jobs_core_module,
            "datetime",
            type("_DT", (), {"now": staticmethod(lambda tz=None: fixed_now)}),
        )

        medicine = await _add_medicine(db_session, course_duration=10, schedules_list=["09:00"])
        call, message = _fake_early_call(user_id=1, medicine_id=medicine.id)
        state = _fake_state()

        before = dict(_manual_reminder_today)
        await mark_taken_early(call, state, db_session)

        # Nothing left to suppress today — the dict must be untouched.
        assert _manual_reminder_today == before
