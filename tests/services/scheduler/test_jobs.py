"""
Tests for the services/scheduler package, focused on the repeat-reminder
lifecycle: adding a repeat job, cancelling it, and making sure cancellation
is properly awaited (regression test for the sync-fire-and-forget -> async
fix).
"""

from unittest.mock import AsyncMock

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
        scheduler_jobs_module._manual_reminder_today[medicine_id] = "some-date-placeholder"

        remove_reminders(medicine_id)

        assert medicine_id not in scheduler_jobs_module._manual_reminder_today


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
