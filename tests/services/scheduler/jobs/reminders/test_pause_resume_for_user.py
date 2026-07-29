"""
Tests for services/scheduler/jobs/reminders.py:
pause_repeat_reminders_for_user() / resume_repeat_reminders_for_user()
— called immediately when a user flips the repeat-reminders setting, so the
change takes effect for reminders that are already in flight, not just
future ones.
"""

from unittest.mock import AsyncMock, patch

from services.scheduler import jobs as scheduler_jobs_module


def _pending_reminder_json(sent_at: str = "2026-01-01T00:00:00+00:00") -> str:
    return (
        '{"message_id": 1, "medicine_name": "Aspirin", "course_duration": 5, '
        f'"language": "en", "timezone": "Europe/Kyiv", "sent_at": "{sent_at}"}}'
    )


class TestPauseRepeatRemindersForUser:
    async def test_removes_the_running_repeat_job_for_that_user(self, mock_redis, mock_bot):
        async def fake_scan_iter(match=None):
            yield "pending_reminder:100:1"

        mock_redis.scan_iter = fake_scan_iter
        mock_redis.get = AsyncMock(return_value=_pending_reminder_json())
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id="repeat_1_100")

        stopped = await scheduler_jobs_module.pause_repeat_reminders_for_user(100)

        assert stopped == 1
        assert scheduler_jobs_module.scheduler.get_job("repeat_1_100") is None

    async def test_does_not_touch_other_users_repeat_jobs(self, mock_redis, mock_bot):
        async def fake_scan_iter(match=None):
            yield "pending_reminder:100:1"
            yield "pending_reminder:200:2"

        mock_redis.scan_iter = fake_scan_iter

        async def fake_get(key):
            if key == "pending_reminder:100:1":
                return _pending_reminder_json()
            return _pending_reminder_json()

        mock_redis.get = fake_get
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id="repeat_1_100")
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id="repeat_2_200")

        stopped = await scheduler_jobs_module.pause_repeat_reminders_for_user(100)

        assert stopped == 1
        assert scheduler_jobs_module.scheduler.get_job("repeat_1_100") is None
        assert scheduler_jobs_module.scheduler.get_job("repeat_2_200") is not None

    async def test_no_pending_reminders_stops_nothing_and_does_not_raise(self, mock_redis, mock_bot):
        stopped = await scheduler_jobs_module.pause_repeat_reminders_for_user(100)

        assert stopped == 0

    async def test_pending_reminder_with_no_active_job_is_a_no_op(self, mock_redis, mock_bot):
        async def fake_scan_iter(match=None):
            yield "pending_reminder:100:1"

        mock_redis.scan_iter = fake_scan_iter
        mock_redis.get = AsyncMock(return_value=_pending_reminder_json())

        stopped = await scheduler_jobs_module.pause_repeat_reminders_for_user(100)

        assert stopped == 0


class TestResumeRepeatRemindersForUser:
    async def test_reschedules_a_job_for_a_pending_reminder_with_no_active_job(self, mock_redis, mock_bot):
        async def fake_scan_iter(match=None):
            yield "pending_reminder:100:1"

        mock_redis.scan_iter = fake_scan_iter
        mock_redis.get = AsyncMock(return_value=_pending_reminder_json())

        resumed = await scheduler_jobs_module.resume_repeat_reminders_for_user(mock_bot, 100)

        assert resumed == 1
        assert scheduler_jobs_module.scheduler.get_job("repeat_1_100") is not None

    async def test_skips_a_reminder_that_already_has_a_running_job(self, mock_redis, mock_bot):
        async def fake_scan_iter(match=None):
            yield "pending_reminder:100:1"

        mock_redis.scan_iter = fake_scan_iter
        mock_redis.get = AsyncMock(return_value=_pending_reminder_json())
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id="repeat_1_100")

        resumed = await scheduler_jobs_module.resume_repeat_reminders_for_user(mock_bot, 100)

        assert resumed == 0
        jobs = [job for job in scheduler_jobs_module.scheduler.get_jobs() if job.id == "repeat_1_100"]
        assert len(jobs) == 1

    async def test_does_not_touch_other_users_pending_reminders(self, mock_redis, mock_bot):
        async def fake_scan_iter(match=None):
            yield "pending_reminder:100:1"
            yield "pending_reminder:200:2"

        mock_redis.scan_iter = fake_scan_iter
        mock_redis.get = AsyncMock(return_value=_pending_reminder_json())

        resumed = await scheduler_jobs_module.resume_repeat_reminders_for_user(mock_bot, 100)

        assert resumed == 1
        assert scheduler_jobs_module.scheduler.get_job("repeat_1_100") is not None
        assert scheduler_jobs_module.scheduler.get_job("repeat_2_200") is None

    async def test_no_pending_reminders_resumes_nothing_and_does_not_raise(self, mock_redis, mock_bot):
        resumed = await scheduler_jobs_module.resume_repeat_reminders_for_user(mock_bot, 100)

        assert resumed == 0

    async def test_next_run_aligns_to_original_cadence_not_now_plus_one_hour(self, mock_redis, mock_bot):
        """
        Reminder sent at 11:00, user re-enables at 12:30 -> next repeat
        should land at 13:00 (sent_at + 1h), not 13:30 (now + 1h).
        """
        from datetime import datetime, timedelta, timezone

        sent_at = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        now = sent_at + timedelta(hours=1, minutes=30)  # 12:30, 30 min after the 1h mark

        async def fake_scan_iter(match=None):
            yield "pending_reminder:100:1"

        mock_redis.scan_iter = fake_scan_iter
        mock_redis.get = AsyncMock(return_value=_pending_reminder_json(sent_at=sent_at.isoformat()))

        with patch("services.scheduler.jobs.reminders.datetime") as mock_datetime:
            mock_datetime.now.return_value = now
            mock_datetime.fromisoformat = datetime.fromisoformat

            await scheduler_jobs_module.resume_repeat_reminders_for_user(mock_bot, 100)

        job = scheduler_jobs_module.scheduler.get_job("repeat_1_100")
        assert job is not None
        assert job.next_run_time == sent_at + timedelta(hours=2)  # 13:00 — the next grid slot after 12:30

    async def test_fires_immediately_if_more_than_an_hour_has_already_passed(self, mock_redis, mock_bot):
        """
        Reminder sent at 11:00, user re-enables at 14:00 (well past the 1h
        mark) -> next repeat should fire right away, not wait until 15:00.
        """
        from datetime import datetime, timedelta, timezone

        sent_at = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        now = sent_at + timedelta(hours=3)  # 14:00

        async def fake_scan_iter(match=None):
            yield "pending_reminder:100:1"

        mock_redis.scan_iter = fake_scan_iter
        mock_redis.get = AsyncMock(return_value=_pending_reminder_json(sent_at=sent_at.isoformat()))

        with patch("services.scheduler.jobs.reminders.datetime") as mock_datetime:
            mock_datetime.now.return_value = now
            mock_datetime.fromisoformat = datetime.fromisoformat

            await scheduler_jobs_module.resume_repeat_reminders_for_user(mock_bot, 100)

        job = scheduler_jobs_module.scheduler.get_job("repeat_1_100")
        assert job is not None
        assert job.next_run_time == now
