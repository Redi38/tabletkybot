"""
Tests for services/scheduler/jobs/reminders.py: send_reminder()'s manual
"send now" suppression (targeting a specific dose schedule) and the
"disable repeat reminders" user setting toggle.
"""

from services.scheduler import jobs as scheduler_jobs_module
from tests.services.scheduler.jobs.reminders._helpers import _FakeSessionFactory


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
