"""
Tests for services/scheduler/jobs/reminders.py: sending a dose reminder
(including the manual-send suppression and stock-triggered auto-archive
branches), the hourly repeat-until-acknowledged resend, cancelling a
repeat job, resuming pending reminders after a restart, and removing all
jobs for a medicine.
"""

from unittest.mock import MagicMock

from services.scheduler import jobs as scheduler_jobs_module


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

        await scheduler_jobs_module.cancel_repeat_reminder(chat_id, medicine_id)

        assert scheduler_jobs_module.scheduler.get_job(job_id) is None

    async def test_awaits_redis_delete(self, mock_redis):
        chat_id, medicine_id = 111, 42

        await scheduler_jobs_module.cancel_repeat_reminder(chat_id, medicine_id)

        mock_redis.delete.assert_awaited_once()

    async def test_no_error_when_job_does_not_exist(self, mock_redis):
        await scheduler_jobs_module.cancel_repeat_reminder(chat_id=999, medicine_id=999)
        mock_redis.delete.assert_awaited_once()

    async def test_deletes_correct_redis_key(self, mock_redis):
        chat_id, medicine_id = 555, 77

        await scheduler_jobs_module.cancel_repeat_reminder(chat_id, medicine_id)

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

        scheduler_jobs_module.remove_reminders(medicine_id)

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

        scheduler_jobs_module.remove_reminders(medicine_id)

        assert (medicine_id, 1) not in scheduler_jobs_module._manual_reminder_today
        assert (medicine_id, 2) not in scheduler_jobs_module._manual_reminder_today
        assert (999, 1) in scheduler_jobs_module._manual_reminder_today  # unrelated medicine untouched


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


class TestSendRepeatReminder:
    """
    Coverage for send_repeat_reminder() — the hourly resend that fires
    until the user presses take/skip: deletes the previous reminder
    message and sends a fresh one instead of it.
    """

    async def test_no_pending_reminder_removes_the_repeat_job_and_sends_nothing(self, mock_redis, mock_bot):
        mock_redis.get.return_value = None
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id="repeat_1_100")

        await scheduler_jobs_module.send_repeat_reminder(mock_bot, medicine_id=1, chat_id=100)

        assert scheduler_jobs_module.scheduler.get_job("repeat_1_100") is None
        mock_bot.send_message.assert_not_awaited()

    async def test_no_pending_reminder_and_no_job_scheduled_does_not_error(self, mock_redis, mock_bot):
        mock_redis.get.return_value = None

        await scheduler_jobs_module.send_repeat_reminder(mock_bot, medicine_id=1, chat_id=100)

        mock_bot.send_message.assert_not_awaited()

    async def test_deletes_previous_message_and_sends_a_new_one(self, mock_redis, mock_bot):
        import json
        from unittest.mock import AsyncMock

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
        from unittest.mock import AsyncMock

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
        from unittest.mock import AsyncMock

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
        from unittest.mock import AsyncMock

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
        from unittest.mock import AsyncMock

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


class TestLocalToday:
    def test_falls_back_to_kyiv_on_invalid_timezone(self):
        # Should not raise regardless of the bogus timezone string.
        result = scheduler_jobs_module._local_today("Not/A_Real_Timezone")
        from datetime import date

        assert isinstance(result, date)


class TestSendReminderAutoArchiveNotificationFailure:
    async def test_survives_a_failed_notification_send(self, mock_redis, mock_bot, db_session):
        from unittest.mock import AsyncMock

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
        mock_bot.send_message.side_effect = RuntimeError("Telegram is down")

        await scheduler_jobs_module.send_reminder(
            bot=mock_bot,
            medicine_id=medicine.id,
            medicine_name="Ibuprofen",
            chat_id=100,
            course_duration=5,
            language="en",
            session_factory=_FakeSessionFactory(db_session),
        )  # must not raise — the medicine is still archived even if the notice fails

        refreshed = await crud.get_user_medicines(db_session, 100, active_only=False)
        assert refreshed[0].is_active is False


class TestSendRepeatReminderFailurePaths:
    async def test_survives_a_delete_message_error_that_is_not_bad_request(self, mock_redis, mock_bot):
        import json
        from unittest.mock import AsyncMock

        pending = {
            "message_id": 555,
            "medicine_name": "Aspirin",
            "course_duration": 5,
            "language": "en",
            "timezone": "Europe/Kyiv",
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(pending))
        mock_bot.delete_message.side_effect = RuntimeError("network blip")
        mock_bot.send_message.return_value.message_id = 777

        await scheduler_jobs_module.send_repeat_reminder(mock_bot, medicine_id=1, chat_id=100)

        mock_bot.send_message.assert_awaited_once()

    async def test_survives_a_send_message_failure(self, mock_redis, mock_bot):
        import json
        from unittest.mock import AsyncMock

        pending = {
            "message_id": 555,
            "medicine_name": "Aspirin",
            "course_duration": 5,
            "language": "en",
            "timezone": "Europe/Kyiv",
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(pending))
        mock_bot.send_message.side_effect = RuntimeError("Telegram is down")

        await scheduler_jobs_module.send_repeat_reminder(mock_bot, medicine_id=1, chat_id=100)  # should not raise


class TestResumePendingRemindersNaiveTimestamp:
    async def test_treats_a_naive_sent_at_as_utc(self, mock_redis, mock_bot):
        from unittest.mock import AsyncMock

        async def fake_scan_iter(match=None):
            yield "pending_reminder:100:1"

        mock_redis.scan_iter = fake_scan_iter
        mock_redis.get = AsyncMock(
            return_value='{"message_id": 1, "medicine_name": "Aspirin", "course_duration": 5, '
            '"language": "en", "timezone": "Europe/Kyiv", "sent_at": "2026-01-01T00:00:00"}'  # no offset
        )

        await scheduler_jobs_module.resume_pending_reminders(mock_bot)

        assert scheduler_jobs_module.scheduler.get_job("repeat_1_100") is not None


class TestRemoveRemindersErrorHandling:
    def test_survives_scheduler_remove_job_error(self, mock_redis, monkeypatch):
        from unittest.mock import MagicMock

        monkeypatch.setattr("asyncio.create_task", lambda coro: coro.close())
        medicine_id = 10
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id=f"med_{medicine_id}_1")
        monkeypatch.setattr(scheduler_jobs_module.scheduler, "remove_job", MagicMock(side_effect=RuntimeError("boom")))

        scheduler_jobs_module.remove_reminders(medicine_id)  # should not raise

    def test_survives_no_running_event_loop(self, mock_redis, monkeypatch):
        def _raise_runtime_error(coro):
            coro.close()
            raise RuntimeError("no running event loop")

        monkeypatch.setattr("asyncio.create_task", _raise_runtime_error)

        scheduler_jobs_module.remove_reminders(10)  # should not raise


class TestSendReminderAutoArchive:
    """
    Coverage for the auto-archive branch in send_reminder(): if the
    empty-stock alert from the previous dose is still unacknowledged when
    the next dose reminder would fire, the medicine is archived instead of
    sending a normal reminder.
    """

    async def test_archives_medicine_instead_of_sending_a_normal_reminder(self, mock_redis, mock_bot, db_session):
        from unittest.mock import AsyncMock

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
        from unittest.mock import AsyncMock

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
