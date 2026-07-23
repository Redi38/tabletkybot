"""
Tests for the services/scheduler package, focused on the repeat-reminder
lifecycle: adding a repeat job, cancelling it, and making sure cancellation
is properly awaited (regression test for the sync-fire-and-forget -> async
fix).
"""

from unittest.mock import AsyncMock, MagicMock

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


class TestSyncReminders:
    """
    Coverage for sync_reminders() — the full DB<->scheduler reconciliation
    that runs at startup and hourly. This is exactly the function whose
    per-medicine loop used to be O(medicines * total_jobs) (see
    TestAddRemindersForMedicineIdempotency above for the underlying fix);
    these tests pin its actual observable behaviour end-to-end.
    """

    async def _add_user_with_medicine(
        self, db_session, user_id, schedule_times=("09:00",), timezone=None, is_active=True
    ):
        from database import crud

        await crud.get_or_create_user(db_session, user_id, f"user{user_id}", f"User {user_id}")
        if timezone:
            await crud.update_user_timezone(db_session, user_id, timezone)
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

    async def test_creates_jobs_for_all_active_medicines_across_users(self, mock_redis, mock_bot, db_session):
        med1 = await self._add_user_with_medicine(db_session, user_id=1, schedule_times=("09:00",))
        med2 = await self._add_user_with_medicine(db_session, user_id=2, schedule_times=("08:00", "20:00"))

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))

        job_ids = {job.id for job in scheduler_jobs_module.scheduler.get_jobs()}
        assert f"med_{med1.id}_{med1.schedules[0].id}" in job_ids
        assert f"med_{med2.id}_{med2.schedules[0].id}" in job_ids
        assert f"med_{med2.id}_{med2.schedules[1].id}" in job_ids

    async def test_skips_archived_medicines(self, mock_redis, mock_bot, db_session):
        await self._add_user_with_medicine(db_session, user_id=1, is_active=False)

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))

        assert scheduler_jobs_module.scheduler.get_jobs() == []

    async def test_removes_orphaned_med_jobs_no_longer_in_db(self, mock_redis, mock_bot, db_session, monkeypatch):
        monkeypatch.setattr("asyncio.create_task", lambda coro: coro.close())

        # A stale job left over from a medicine that was since deleted straight
        # from the DB (not through remove_reminders) — sync_reminders should
        # clean it up.
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id="med_999_1")

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))

        assert scheduler_jobs_module.scheduler.get_job("med_999_1") is None

    async def test_does_not_remove_unrelated_repeat_jobs(self, mock_redis, mock_bot, db_session):
        # Orphan cleanup only targets the "med_" prefix — an in-flight hourly
        # repeat job (unrelated lifecycle, cleaned up via cancel_repeat_reminder)
        # must survive a full sync untouched.
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id="repeat_1_100")

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))

        assert scheduler_jobs_module.scheduler.get_job("repeat_1_100") is not None

    async def test_falls_back_to_kyiv_timezone_when_user_has_none_set(self, mock_redis, mock_bot, db_session):
        await self._add_user_with_medicine(db_session, user_id=1, timezone=None)

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))

        job = next(iter(scheduler_jobs_module.scheduler.get_jobs()))
        assert str(job.trigger.timezone) == "Europe/Kyiv"

    async def test_uses_the_users_configured_timezone(self, mock_redis, mock_bot, db_session):
        await self._add_user_with_medicine(db_session, user_id=1, timezone="America/New_York")

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))

        job = next(iter(scheduler_jobs_module.scheduler.get_jobs()))
        assert str(job.trigger.timezone) == "America/New_York"

    async def test_running_sync_twice_is_idempotent(self, mock_redis, mock_bot, db_session):
        med = await self._add_user_with_medicine(db_session, user_id=1)

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))
        first_ids = {job.id for job in scheduler_jobs_module.scheduler.get_jobs()}

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))
        second_ids = {job.id for job in scheduler_jobs_module.scheduler.get_jobs()}

        assert first_ids == second_ids == {f"med_{med.id}_{med.schedules[0].id}"}

    async def test_no_medicines_at_all_leaves_scheduler_empty(self, mock_redis, mock_bot, db_session):
        from database import crud

        await crud.get_or_create_user(db_session, 1, "user1", "User 1")
        await db_session.commit()

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))

        assert scheduler_jobs_module.scheduler.get_jobs() == []


class TestSyncSingleReminder:
    """
    Coverage for sync_single_reminder() — the point-signal handler used by
    the Admin Panel (add/edit/delete a medicine, or "send now").
    """

    async def _add_user_with_medicine(self, db_session, user_id=1, schedule_times=("09:00",)):
        from database import crud

        await crud.get_or_create_user(db_session, user_id, f"user{user_id}", f"User {user_id}")
        medicine = await crud.add_medicine(
            db_session,
            user_id=user_id,
            name="Ibuprofen",
            form="tablets",
            dosage="200mg",
            schedules_list=list(schedule_times),
            course_duration=5,
        )
        await db_session.commit()
        return medicine

    async def test_delete_action_removes_reminders_without_touching_db(
        self, mock_redis, mock_bot, db_session, monkeypatch
    ):
        monkeypatch.setattr("asyncio.create_task", lambda coro: coro.close())
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id="med_5_1")

        await scheduler_jobs_module.sync_single_reminder(mock_bot, _FakeSessionFactory(db_session), 5, "delete")

        assert scheduler_jobs_module.scheduler.get_job("med_5_1") is None

    async def test_edit_action_reschedules_the_medicine(self, mock_redis, mock_bot, db_session):
        medicine = await self._add_user_with_medicine(db_session)

        await scheduler_jobs_module.sync_single_reminder(mock_bot, _FakeSessionFactory(db_session), medicine.id, "edit")

        assert scheduler_jobs_module.scheduler.get_job(f"med_{medicine.id}_{medicine.schedules[0].id}") is not None

    async def test_send_now_action_sends_immediately_without_scheduling_a_cron_job(
        self, mock_redis, mock_bot, db_session
    ):
        medicine = await self._add_user_with_medicine(db_session)
        mock_bot.send_message.return_value.message_id = 123

        await scheduler_jobs_module.sync_single_reminder(
            mock_bot, _FakeSessionFactory(db_session), medicine.id, "send_now"
        )

        mock_bot.send_message.assert_awaited_once()
        # "send_now" is a one-off manual push, not a resync — no med_* cron job created
        assert scheduler_jobs_module.scheduler.get_job(f"med_{medicine.id}_{medicine.schedules[0].id}") is None

    async def test_medicine_missing_from_db_removes_any_leftover_reminders(
        self, mock_redis, mock_bot, db_session, monkeypatch
    ):
        monkeypatch.setattr("asyncio.create_task", lambda coro: coro.close())
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id="med_404_1")

        await scheduler_jobs_module.sync_single_reminder(mock_bot, _FakeSessionFactory(db_session), 404, "edit")

        assert scheduler_jobs_module.scheduler.get_job("med_404_1") is None


class TestSendRepeatReminder:
    """
    Coverage for send_repeat_reminder() — the hourly resend that fires
    until the user presses take/skip: deletes the previous reminder
    message and sends a fresh one instead of it.
    """

    async def test_no_pending_reminder_removes_the_repeat_job_and_sends_nothing(self, mock_redis, mock_bot):
        mock_redis.get = AsyncMock(return_value=None)
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id="repeat_1_100")

        await scheduler_jobs_module.send_repeat_reminder(mock_bot, medicine_id=1, chat_id=100)

        assert scheduler_jobs_module.scheduler.get_job("repeat_1_100") is None
        mock_bot.send_message.assert_not_awaited()

    async def test_no_pending_reminder_and_no_job_scheduled_does_not_error(self, mock_redis, mock_bot):
        mock_redis.get = AsyncMock(return_value=None)

        await scheduler_jobs_module.send_repeat_reminder(mock_bot, medicine_id=1, chat_id=100)

        mock_bot.send_message.assert_not_awaited()

    async def test_deletes_previous_message_and_sends_a_new_one(self, mock_redis, mock_bot):
        import json

        pending = {
            "message_id": 555,
            "medicine_name": "Aspirin",
            "course_duration": 5,
            "language": "en",
            "timezone": "Europe/Kyiv",
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(pending))
        mock_bot.send_message.return_value.message_id = 777

        await scheduler_jobs_module.send_repeat_reminder(mock_bot, medicine_id=1, chat_id=100)

        mock_bot.delete_message.assert_awaited_once_with(chat_id=100, message_id=555)
        mock_bot.send_message.assert_awaited_once()

    async def test_survives_delete_message_failure(self, mock_redis, mock_bot):
        import json

        from aiogram.exceptions import TelegramBadRequest

        pending = {
            "message_id": 555,
            "medicine_name": "Aspirin",
            "course_duration": 5,
            "language": "en",
            "timezone": "Europe/Kyiv",
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(pending))
        mock_bot.delete_message.side_effect = TelegramBadRequest(method=MagicMock(), message="not found")
        mock_bot.send_message.return_value.message_id = 777

        await scheduler_jobs_module.send_repeat_reminder(mock_bot, medicine_id=1, chat_id=100)

        # A failed delete (message too old / already gone) must not stop the
        # new repeat reminder from still being sent.
        mock_bot.send_message.assert_awaited_once()


class TestResumePendingReminders:
    """
    Coverage for resume_pending_reminders() — called once at startup to
    restore hourly repeat jobs for every still-unacknowledged reminder,
    preserving the original cadence instead of resetting it to "now + 1h".
    """

    async def test_restores_a_job_for_each_pending_reminder(self, mock_redis, mock_bot):
        async def fake_scan_iter(match=None):
            for key in ["pending_reminder:100:1"]:
                yield key

        mock_redis.scan_iter = fake_scan_iter
        mock_redis.get = AsyncMock(
            return_value='{"message_id": 1, "medicine_name": "Aspirin", "course_duration": 5, '
            '"language": "en", "timezone": "Europe/Kyiv", "sent_at": "2026-01-01T00:00:00+00:00"}'
        )

        await scheduler_jobs_module.resume_pending_reminders(mock_bot)

        assert scheduler_jobs_module.scheduler.get_job("repeat_1_100") is not None

    async def test_skips_reminders_that_already_have_a_running_repeat_job(self, mock_redis, mock_bot):
        async def fake_scan_iter(match=None):
            for key in ["pending_reminder:100:1"]:
                yield key

        mock_redis.scan_iter = fake_scan_iter
        mock_redis.get = AsyncMock(
            return_value='{"message_id": 1, "medicine_name": "Aspirin", "course_duration": 5, '
            '"language": "en", "timezone": "Europe/Kyiv", "sent_at": "2026-01-01T00:00:00+00:00"}'
        )
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id="repeat_1_100")

        # Should not raise / duplicate — replace_existing isn't even reached
        # because of the early `continue` when the job already exists.
        await scheduler_jobs_module.resume_pending_reminders(mock_bot)

        jobs = [job for job in scheduler_jobs_module.scheduler.get_jobs() if job.id == "repeat_1_100"]
        assert len(jobs) == 1

    async def test_no_pending_reminders_restores_nothing(self, mock_redis, mock_bot):
        await scheduler_jobs_module.resume_pending_reminders(mock_bot)

        assert scheduler_jobs_module.scheduler.get_jobs() == []

    async def test_malformed_sent_at_falls_back_gracefully_instead_of_raising(self, mock_redis, mock_bot):
        async def fake_scan_iter(match=None):
            for key in ["pending_reminder:100:1"]:
                yield key

        mock_redis.scan_iter = fake_scan_iter
        mock_redis.get = AsyncMock(
            return_value='{"message_id": 1, "medicine_name": "Aspirin", "course_duration": 5, '
            '"language": "en", "timezone": "Europe/Kyiv", "sent_at": "not-a-real-timestamp"}'
        )

        await scheduler_jobs_module.resume_pending_reminders(mock_bot)

        assert scheduler_jobs_module.scheduler.get_job("repeat_1_100") is not None


class TestSendReminderAutoArchive:
    """
    Coverage for the auto-archive branch in send_reminder(): if the
    empty-stock alert from the previous dose is still unacknowledged when
    the next dose reminder would fire, the medicine is archived instead of
    sending a normal reminder.
    """

    async def test_archives_medicine_instead_of_sending_a_normal_reminder(self, mock_redis, mock_bot, db_session):
        from database import crud

        await crud.get_or_create_user(db_session, 100, "tester", "Test User")
        medicine = await crud.add_medicine(
            db_session,
            user_id=100,
            name="Ibuprofen",
            form="tablets",
            dosage="200mg",
            schedules_list=["09:00"],
            course_duration=5,
        )
        await db_session.commit()

        mock_redis.get = AsyncMock(return_value='{"medicine_name": "Ibuprofen", "language": "en"}')

        await scheduler_jobs_module.send_reminder(
            bot=mock_bot,
            medicine_id=medicine.id,
            medicine_name="Ibuprofen",
            chat_id=100,
            course_duration=5,
            language="en",
            session_factory=_FakeSessionFactory(db_session),
        )

        refreshed = await crud.get_user_medicines(db_session, 100, active_only=False)
        assert refreshed[0].is_active is False
        mock_bot.send_message.assert_awaited_once()
        # No dose-reminder keyboard — the auto-archive notice has no reply_markup
        assert "reply_markup" not in mock_bot.send_message.call_args.kwargs

    async def test_is_manual_sends_ignore_the_stock_alert_check(self, mock_redis, mock_bot, db_session):
        from database import crud

        await crud.get_or_create_user(db_session, 100, "tester", "Test User")
        medicine = await crud.add_medicine(
            db_session,
            user_id=100,
            name="Ibuprofen",
            form="tablets",
            dosage="200mg",
            schedules_list=["09:00"],
            course_duration=5,
        )
        await db_session.commit()
        mock_bot.send_message.return_value.message_id = 123
        mock_redis.get = AsyncMock(return_value='{"medicine_name": "Ibuprofen", "language": "en"}')

        await scheduler_jobs_module.send_reminder(
            bot=mock_bot,
            medicine_id=medicine.id,
            medicine_name="Ibuprofen",
            chat_id=100,
            course_duration=5,
            language="en",
            is_manual=True,
            session_factory=_FakeSessionFactory(db_session),
        )

        # is_manual=True skips the auto-archive branch entirely (it's guarded
        # by `not is_manual`), so a normal reminder with buttons goes out.
        refreshed = await crud.get_user_medicines(db_session, 100, active_only=False)
        assert refreshed[0].is_active is True
        assert "reply_markup" in mock_bot.send_message.call_args.kwargs


class TestSchedulerStartStop:
    async def test_start_scheduler_is_idempotent(self, mock_redis):
        scheduler_jobs_module.start_scheduler()
        assert scheduler_jobs_module.scheduler.running is True
        scheduler_jobs_module.start_scheduler()  # second call must not raise
        assert scheduler_jobs_module.scheduler.running is True
        scheduler_jobs_module.stop_scheduler()

    async def test_stop_scheduler_is_idempotent(self, mock_redis):
        scheduler_jobs_module.stop_scheduler()  # never started — must not raise
        assert scheduler_jobs_module.scheduler.running is False
