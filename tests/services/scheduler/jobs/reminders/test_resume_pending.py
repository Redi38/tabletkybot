"""
Tests for services/scheduler/jobs/reminders.py: resume_pending_reminders()
— called once at startup to restore hourly repeat jobs for every
still-unacknowledged reminder, preserving the original cadence instead of
resetting it to "now + 1h", including malformed/naive `sent_at` handling.
"""

from unittest.mock import AsyncMock

from services.scheduler import jobs as scheduler_jobs_module


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


class TestResumePendingRemindersNaiveTimestamp:
    async def test_treats_a_naive_sent_at_as_utc(self, mock_redis, mock_bot):
        async def fake_scan_iter(match=None):
            yield "pending_reminder:100:1"

        mock_redis.scan_iter = fake_scan_iter
        mock_redis.get = AsyncMock(
            return_value='{"message_id": 1, "medicine_name": "Aspirin", "course_duration": 5, '
            '"language": "en", "timezone": "Europe/Kyiv", "sent_at": "2026-01-01T00:00:00"}'  # no offset
        )

        await scheduler_jobs_module.resume_pending_reminders(mock_bot)

        assert scheduler_jobs_module.scheduler.get_job("repeat_1_100") is not None
