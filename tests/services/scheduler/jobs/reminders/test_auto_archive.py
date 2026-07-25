"""
Tests for services/scheduler/jobs/reminders.py: the stock-triggered
auto-archive branch of send_reminder() — archiving a medicine instead of
sending a normal reminder when the previous dose's empty-stock alert is
still unacknowledged, surviving a failed notification send, and manual
sends bypassing the check entirely.
"""

from unittest.mock import AsyncMock

from services.scheduler import jobs as scheduler_jobs_module
from tests.services.scheduler.jobs.reminders._helpers import _FakeSessionFactory


class TestSendReminderAutoArchiveNotificationFailure:
    async def test_survives_a_failed_notification_send(self, mock_redis, mock_bot, db_session):
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
