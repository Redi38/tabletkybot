"""
Tests for services/scheduler/jobs/reminders.py: send_repeat_reminder() —
the hourly resend that fires until the user presses take/skip (deletes
the previous reminder message and sends a fresh one instead), and its
failure paths (delete/send errors that must not propagate).
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from services.scheduler import jobs as scheduler_jobs_module


class TestSendRepeatReminder:
    """
    Coverage for send_repeat_reminder() — the hourly resend that fires
    until the user presses take/skip: deletes the previous reminder
    message and sends a fresh one instead of it.
    """

    async def test_no_pending_reminder_removes_the_repeat_job_and_sends_nothing(self, mock_redis, mock_bot):
        mock_redis.get.return_value = None
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id="repeat_1_0_100")

        await scheduler_jobs_module.send_repeat_reminder(mock_bot, medicine_id=1, chat_id=100)

        assert scheduler_jobs_module.scheduler.get_job("repeat_1_0_100") is None
        mock_bot.send_message.assert_not_awaited()

    async def test_no_pending_reminder_and_no_job_scheduled_does_not_error(self, mock_redis, mock_bot):
        mock_redis.get.return_value = None

        await scheduler_jobs_module.send_repeat_reminder(mock_bot, medicine_id=1, chat_id=100)

        mock_bot.send_message.assert_not_awaited()

    async def test_deletes_previous_message_and_sends_a_new_one(self, mock_redis, mock_bot):
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


class TestSendRepeatReminderFailurePaths:
    async def test_survives_a_delete_message_error_that_is_not_bad_request(self, mock_redis, mock_bot):
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


class TestBlockedUserCleansUpPendingReminder:
    """
    Regression coverage: skipping a send for a blocked user must also clear
    the pending-reminder entry in Redis, not just cancel the APScheduler
    job — otherwise the medicine sits in the admin Reminder Queue forever,
    since a blocked user can never acknowledge it.
    """

    async def test_send_repeat_reminder_deletes_the_pending_entry_for_a_blocked_user(self, mock_redis, mock_bot):
        session_factory = MagicMock()
        session_factory.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("database.crud.get_user_blocked", AsyncMock(return_value=True)):
            scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id="repeat_1_0_100")

            await scheduler_jobs_module.send_repeat_reminder(
                mock_bot, medicine_id=1, chat_id=100, session_factory=session_factory
            )

        mock_redis.delete.assert_awaited_once_with("pending_reminder:100:1:0")
        assert scheduler_jobs_module.scheduler.get_job("repeat_1_0_100") is None
        mock_bot.send_message.assert_not_awaited()

    async def test_send_reminder_deletes_a_stale_pending_entry_for_a_blocked_user(self, mock_redis, mock_bot):
        session_factory = MagicMock()
        session_factory.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("database.crud.get_user_blocked", AsyncMock(return_value=True)):
            await scheduler_jobs_module.send_reminder(
                bot=mock_bot,
                medicine_id=1,
                medicine_name="Aspirin",
                chat_id=100,
                course_duration=5,
                language="en",
                session_factory=session_factory,
            )

        mock_redis.delete.assert_awaited_once_with("pending_reminder:100:1:0")
        mock_bot.send_message.assert_not_awaited()
