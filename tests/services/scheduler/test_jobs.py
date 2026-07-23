"""
Tests for the services/scheduler package, focused on the repeat-reminder
lifecycle: adding a repeat job, cancelling it, and making sure cancellation
is properly awaited.
"""

from unittest.mock import AsyncMock

from database.models import Medicine, MedicineSchedule
from services.scheduler import acquire_action_lock, cancel_repeat_reminder, remove_reminders
from services.scheduler import jobs as scheduler_jobs_module
from services.scheduler import redis_state as scheduler_redis_module


class TestCancelRepeatReminder:
    async def test_removes_scheduler_job(self, mock_redis):
        chat_id, medicine_id = 111, 42
        job_id = f"repeat_{medicine_id}_{chat_id}"

        scheduler_jobs_module.scheduler.add_job(
            lambda: None,
            trigger="interval",
            hours=1,
            id=job_id,
        )
        assert scheduler_jobs_module.scheduler.get_job(job_id) is not None

        await cancel_repeat_reminder(chat_id, medicine_id)

        assert scheduler_jobs_module.scheduler.get_job(job_id) is None

    async def test_awaits_redis_delete(self, mock_redis):
        chat_id, medicine_id = 111, 42

        await cancel_repeat_reminder(chat_id, medicine_id)

        mock_redis.delete.assert_awaited_once()

    async def test_no_error_when_job_does_not_exist(self, mock_redis):
        await cancel_repeat_reminder(chat_id=999, medicine_id=999)
        mock_redis.delete.assert_awaited_once()

    async def test_deletes_correct_redis_key(self, mock_redis):
        chat_id, medicine_id = 555, 77

        await cancel_repeat_reminder(chat_id, medicine_id)

        called_key = mock_redis.delete.call_args[0][0]
        assert called_key == f"pending_reminder:{chat_id}:{medicine_id}"


class TestRemoveReminders:
    def test_removes_both_med_and_repeat_jobs(self, mock_redis, monkeypatch):
        monkeypatch.setattr("asyncio.create_task", lambda coro: coro.close())

        medicine_id = 10
        scheduler_jobs_module.scheduler.add_job(
            lambda: None,
            trigger="interval",
            hours=1,
            id=f"med_{medicine_id}_1",
        )
        scheduler_jobs_module.scheduler.add_job(
            lambda: None,
            trigger="interval",
            hours=1,
            id=f"repeat_{medicine_id}_555",
        )
        scheduler_jobs_module.scheduler.add_job(
            lambda: None,
            trigger="interval",
            hours=1,
            id="med_999_1",
        )

        remove_reminders(medicine_id)

        remaining_ids = {job.id for job in scheduler_jobs_module.scheduler.get_jobs()}
        assert f"med_{medicine_id}_1" not in remaining_ids
        assert f"repeat_{medicine_id}_555" not in remaining_ids
        assert "med_999_1" in remaining_ids

    def test_clears_manual_reminder_flag(self, mock_redis, monkeypatch):
        monkeypatch.setattr("asyncio.create_task", lambda coro: coro.close())

        medicine_id = 10
        scheduler_jobs_module._manual_reminder_today[(medicine_id, 1)] = "some-date-placeholder"
        scheduler_jobs_module._manual_reminder_today[(medicine_id, 2)] = "some-date-placeholder"
        scheduler_jobs_module._manual_reminder_today[(999, 1)] = "some-date-placeholder"

        remove_reminders(medicine_id)

        assert (medicine_id, 1) not in scheduler_jobs_module._manual_reminder_today
        assert (medicine_id, 2) not in scheduler_jobs_module._manual_reminder_today
        assert (999, 1) in scheduler_jobs_module._manual_reminder_today  # unrelated medicine untouched


class TestPendingReminderRedisHelpers:
    async def test_save_and_get_roundtrip(self, mock_redis):
        stored = {}

        async def fake_set(key, value, ex=None):
            stored["key"] = key
            stored["value"] = value
            return True

        async def fake_get(key):
            return stored.get("value")

        mock_redis.set = AsyncMock(side_effect=fake_set)
        mock_redis.get = AsyncMock(side_effect=fake_get)

        await scheduler_redis_module._save_pending_reminder(
            chat_id=1,
            medicine_id=2,
            message_id=999,
            medicine_name="Aspirin",
            course_duration=5,
            language="ua",
            timezone="Europe/Kyiv",
        )
        result = await scheduler_redis_module._get_pending_reminder(chat_id=1, medicine_id=2)

        assert result["medicine_name"] == "Aspirin"
        assert result["course_duration"] == 5

    async def test_get_pending_reminder_returns_none_on_malformed_json(self, mock_redis):
        mock_redis.get = AsyncMock(return_value="not-valid-json{")

        result = await scheduler_redis_module._get_pending_reminder(chat_id=1, medicine_id=2)

        assert result is None

    async def test_get_pending_reminder_returns_none_when_missing(self, mock_redis):
        mock_redis.get = AsyncMock(return_value=None)

        result = await scheduler_redis_module._get_pending_reminder(chat_id=1, medicine_id=2)

        assert result is None


class TestAcquireActionLock:
    async def test_first_call_acquires_lock(self, mock_redis):
        mock_redis.set = AsyncMock(return_value=True)

        acquired = await acquire_action_lock(chat_id=1, medicine_id=2)

        assert acquired is True

    async def test_second_call_within_ttl_is_rejected(self, mock_redis):
        mock_redis.set = AsyncMock(return_value=None)

        acquired = await acquire_action_lock(chat_id=1, medicine_id=2)

        assert acquired is False

    async def test_uses_correct_key_and_nx_ex_options(self, mock_redis):
        mock_redis.set = AsyncMock(return_value=True)

        await acquire_action_lock(chat_id=111, medicine_id=42)

        args, kwargs = mock_redis.set.call_args
        assert args[0] == "action_lock:111:42"
        assert kwargs.get("nx") is True
        assert kwargs.get("ex") == 3

    async def test_fails_open_when_redis_not_configured(self, mock_redis):
        scheduler_redis_module._redis_client = None

        acquired = await acquire_action_lock(chat_id=1, medicine_id=2)

        assert acquired is True


class _FakeSchedule:
    def __init__(self, id: int, scheduled_time: str):
        self.id = id
        self.scheduled_time = scheduled_time


class TestNextScheduleIdForToday:
    def test_returns_the_soonest_schedule_after_now(self, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        fixed_now = datetime(2026, 7, 21, 10, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
        monkeypatch.setattr(
            scheduler_jobs_module,
            "datetime",
            type("_DT", (), {"now": staticmethod(lambda tz=None: fixed_now)}),
        )

        schedules = [_FakeSchedule(1, "09:00"), _FakeSchedule(2, "12:00"), _FakeSchedule(3, "21:00")]

        result = scheduler_jobs_module._next_schedule_id_for_today(schedules, "Europe/Kyiv")

        assert result == 2  # 12:00 is the next one after 10:00 — not 09:00 (passed) or 21:00 (further away)

    def test_returns_none_when_all_schedules_today_have_passed(self, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        fixed_now = datetime(2026, 7, 21, 22, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
        monkeypatch.setattr(
            scheduler_jobs_module,
            "datetime",
            type("_DT", (), {"now": staticmethod(lambda tz=None: fixed_now)}),
        )

        schedules = [_FakeSchedule(1, "09:00"), _FakeSchedule(2, "21:00")]

        result = scheduler_jobs_module._next_schedule_id_for_today(schedules, "Europe/Kyiv")

        assert result is None

    def test_falls_back_to_kyiv_on_invalid_timezone(self, monkeypatch):
        # Should not raise, regardless of the (bogus) timezone string
        schedules = [_FakeSchedule(1, "23:59")]
        result = scheduler_jobs_module._next_schedule_id_for_today(schedules, "Not/A_Real_Timezone")
        assert result in (1, None)  # depends on real current time; just must not crash

    def test_skips_malformed_schedule_times(self, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        fixed_now = datetime(2026, 7, 21, 10, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
        monkeypatch.setattr(
            scheduler_jobs_module,
            "datetime",
            type("_DT", (), {"now": staticmethod(lambda tz=None: fixed_now)}),
        )

        schedules = [_FakeSchedule(1, "not-a-time"), _FakeSchedule(2, "12:00")]

        result = scheduler_jobs_module._next_schedule_id_for_today(schedules, "Europe/Kyiv")

        assert result == 2


class TestManualReminderSuppressionTargetsSpecificSchedule:
    """
    Regression coverage for the fix where a manual "send now" from the
    Admin Panel used to suppress whichever scheduled reminder fired next
    for that medicine, regardless of which dose slot it was — incorrect
    for medicines with 3+ daily schedules. The flag is now keyed by
    (medicine_id, schedule_id), so only the intended slot is suppressed.
    """

    async def test_manual_send_only_suppresses_the_targeted_schedule(self, mock_redis, mock_bot):
        medicine_id = 42

        # Simulate a manual send that targeted schedule_id=2 (e.g. the 12:00 slot)
        await scheduler_jobs_module.send_reminder(
            bot=mock_bot,
            medicine_id=medicine_id,
            medicine_name="Ibuprofen",
            chat_id=1,
            course_duration=5,
            language="en",
            is_manual=True,
            schedule_id=2,
        )

        assert scheduler_jobs_module._manual_reminder_today.get((medicine_id, 2)) is not None
        assert (medicine_id, 1) not in scheduler_jobs_module._manual_reminder_today
        assert (medicine_id, 3) not in scheduler_jobs_module._manual_reminder_today

    async def test_scheduled_fire_for_a_different_schedule_is_not_suppressed(self, mock_redis, mock_bot):
        medicine_id = 42
        today = scheduler_jobs_module._local_today("Europe/Kyiv")
        scheduler_jobs_module._manual_reminder_today[(medicine_id, 2)] = today

        # schedule_id=1 (a different slot, e.g. 09:00) fires normally — must NOT be suppressed
        await scheduler_jobs_module.send_reminder(
            bot=mock_bot,
            medicine_id=medicine_id,
            medicine_name="Ibuprofen",
            chat_id=1,
            course_duration=5,
            language="en",
            is_manual=False,
            schedule_id=1,
        )

        mock_bot.send_message.assert_awaited_once()
        # The unrelated schedule_id=2 flag is untouched by schedule_id=1's fire
        assert scheduler_jobs_module._manual_reminder_today.get((medicine_id, 2)) == today

    async def test_scheduled_fire_for_the_targeted_schedule_is_suppressed_once(self, mock_redis, mock_bot):
        medicine_id = 42
        today = scheduler_jobs_module._local_today("Europe/Kyiv")
        scheduler_jobs_module._manual_reminder_today[(medicine_id, 2)] = today

        await scheduler_jobs_module.send_reminder(
            bot=mock_bot,
            medicine_id=medicine_id,
            medicine_name="Ibuprofen",
            chat_id=1,
            course_duration=5,
            language="en",
            is_manual=False,
            schedule_id=2,
        )

        mock_bot.send_message.assert_not_awaited()
        assert (medicine_id, 2) not in scheduler_jobs_module._manual_reminder_today  # consumed

        # A second fire for the SAME schedule today must send normally (flag already consumed)
        await scheduler_jobs_module.send_reminder(
            bot=mock_bot,
            medicine_id=medicine_id,
            medicine_name="Ibuprofen",
            chat_id=1,
            course_duration=5,
            language="en",
            is_manual=False,
            schedule_id=2,
        )
        mock_bot.send_message.assert_awaited_once()


class _FakeSessionFactory:
    """
    Minimal stand-in for `async_sessionmaker` that hands back the same
    already-open test `db_session` via `async with session_factory() as
    session`, instead of opening a brand-new engine/connection.
    """

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc_info):
        return False


class TestSendReminderRepeatEnabledToggle:
    """
    Coverage for the "disable repeat reminders" user setting: send_reminder
    should only schedule the hourly repeat_{medicine_id}_{chat_id} job when
    the user has repeats enabled (the default), and must skip it — without
    erroring — when they've turned it off.
    """

    async def test_schedules_repeat_job_when_no_session_factory_given(self, mock_redis, mock_bot):
        # No session_factory -> repeat_enabled defaults to True (used by manual
        # sends / callers that don't pass one), so the repeat job is still set up.
        mock_bot.send_message.return_value.message_id = 123
        await scheduler_jobs_module.send_reminder(
            bot=mock_bot,
            medicine_id=1,
            medicine_name="Ibuprofen",
            chat_id=100,
            course_duration=5,
            language="en",
        )
        assert scheduler_jobs_module.scheduler.get_job("repeat_1_100") is not None

    async def test_schedules_repeat_job_when_user_has_repeats_enabled(self, mock_redis, mock_bot, db_session):
        from database import crud

        await crud.get_or_create_user(db_session, 100, "a", "A")
        mock_bot.send_message.return_value.message_id = 123

        await scheduler_jobs_module.send_reminder(
            bot=mock_bot,
            medicine_id=1,
            medicine_name="Ibuprofen",
            chat_id=100,
            course_duration=5,
            language="en",
            session_factory=_FakeSessionFactory(db_session),
        )
        assert scheduler_jobs_module.scheduler.get_job("repeat_1_100") is not None

    async def test_skips_repeat_job_when_user_disabled_repeats(self, mock_redis, mock_bot, db_session):
        from database import crud

        await crud.get_or_create_user(db_session, 100, "a", "A")
        await crud.toggle_repeat_reminders(db_session, 100)  # turn off
        mock_bot.send_message.return_value.message_id = 123

        await scheduler_jobs_module.send_reminder(
            bot=mock_bot,
            medicine_id=1,
            medicine_name="Ibuprofen",
            chat_id=100,
            course_duration=5,
            language="en",
            session_factory=_FakeSessionFactory(db_session),
        )

        # The reminder itself must still be sent — only the hourly repeat is skipped.
        mock_bot.send_message.assert_awaited_once()
        assert scheduler_jobs_module.scheduler.get_job("repeat_1_100") is None


class TestAddRemindersForMedicineIdempotency:
    """
    Regression coverage for the O(n^2) fix: add_reminders_for_medicine used
    to rebuild `{job.id for job in scheduler.get_jobs()}` from scratch on
    every call, which meant sync_reminders() re-scanned the *entire* job
    list once per medicine. It's now a direct scheduler.get_job(job_id)
    lookup instead — these tests pin the observable behaviour (idempotent,
    still adds genuinely-new jobs, still skips inactive medicines).
    """

    def _medicine(self, medicine_id=1, schedule_times=("09:00",), is_active=True):
        medicine = Medicine(
            id=medicine_id,
            user_id=100,
            name="Ibuprofen",
            dosage="200mg",
            course_duration=5,
            is_active=is_active,
        )
        medicine.schedules = [
            MedicineSchedule(id=idx + 1, medicine_id=medicine_id, scheduled_time=t)
            for idx, t in enumerate(schedule_times)
        ]
        return medicine

    def test_creates_one_job_per_schedule(self, mock_redis, mock_bot):
        medicine = self._medicine(schedule_times=("09:00", "21:00"))

        scheduler_jobs_module.add_reminders_for_medicine(mock_bot, medicine, "Europe/Kyiv", chat_id=100)

        assert scheduler_jobs_module.scheduler.get_job("med_1_1") is not None
        assert scheduler_jobs_module.scheduler.get_job("med_1_2") is not None

    def test_second_call_is_a_no_op_for_already_scheduled_jobs(self, mock_redis, mock_bot):
        medicine = self._medicine(schedule_times=("09:00",))

        scheduler_jobs_module.add_reminders_for_medicine(mock_bot, medicine, "Europe/Kyiv", chat_id=100)
        jobs_after_first_call = {job.id for job in scheduler_jobs_module.scheduler.get_jobs()}

        # Calling again (as sync_reminders() does on every full sync) must not
        # duplicate or otherwise disturb the already-scheduled job.
        scheduler_jobs_module.add_reminders_for_medicine(mock_bot, medicine, "Europe/Kyiv", chat_id=100)
        jobs_after_second_call = {job.id for job in scheduler_jobs_module.scheduler.get_jobs()}

        assert jobs_after_second_call == jobs_after_first_call == {"med_1_1"}

    def test_adds_only_the_genuinely_new_schedule(self, mock_redis, mock_bot):
        medicine = self._medicine(schedule_times=("09:00",))
        scheduler_jobs_module.add_reminders_for_medicine(mock_bot, medicine, "Europe/Kyiv", chat_id=100)

        # A new schedule slot gets added to the same medicine (e.g. user added a dose)
        medicine.schedules.append(MedicineSchedule(id=2, medicine_id=1, scheduled_time="21:00"))
        scheduler_jobs_module.add_reminders_for_medicine(mock_bot, medicine, "Europe/Kyiv", chat_id=100)

        assert scheduler_jobs_module.scheduler.get_job("med_1_1") is not None
        assert scheduler_jobs_module.scheduler.get_job("med_1_2") is not None

    def test_inactive_medicine_removes_existing_reminders_instead(self, mock_redis, mock_bot, monkeypatch):
        monkeypatch.setattr("asyncio.create_task", lambda coro: coro.close())
        medicine = self._medicine(schedule_times=("09:00",), is_active=True)
        scheduler_jobs_module.add_reminders_for_medicine(mock_bot, medicine, "Europe/Kyiv", chat_id=100)
        assert scheduler_jobs_module.scheduler.get_job("med_1_1") is not None

        medicine.is_active = False
        scheduler_jobs_module.add_reminders_for_medicine(mock_bot, medicine, "Europe/Kyiv", chat_id=100)

        assert scheduler_jobs_module.scheduler.get_job("med_1_1") is None
