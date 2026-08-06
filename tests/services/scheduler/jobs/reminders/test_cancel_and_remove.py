"""
Tests for services/scheduler/jobs/reminders.py: cancelling a repeat job,
removing all jobs for a medicine (including error handling), and the
local-today timezone helper.
"""

from unittest.mock import AsyncMock

from services.scheduler import jobs as scheduler_jobs_module


class TestCancelRepeatReminder:
    async def test_removes_scheduler_job(self, mock_redis):
        chat_id, medicine_id, schedule_id = 111, 42, 7
        job_id = f"repeat_{medicine_id}_{schedule_id}_{chat_id}"

        scheduler_jobs_module.scheduler.add_job(
            lambda: None,
            trigger="interval",
            hours=1,
            id=job_id,
        )
        assert scheduler_jobs_module.scheduler.get_job(job_id) is not None

        await scheduler_jobs_module.cancel_repeat_reminder(chat_id, medicine_id, schedule_id)

        assert scheduler_jobs_module.scheduler.get_job(job_id) is None

    async def test_awaits_redis_delete(self, mock_redis):
        chat_id, medicine_id, schedule_id = 111, 42, 7

        await scheduler_jobs_module.cancel_repeat_reminder(chat_id, medicine_id, schedule_id)

        mock_redis.delete.assert_awaited_once()

    async def test_no_error_when_job_does_not_exist(self, mock_redis):
        await scheduler_jobs_module.cancel_repeat_reminder(chat_id=999, medicine_id=999, schedule_id=999)
        mock_redis.delete.assert_awaited_once()

    async def test_deletes_correct_redis_key(self, mock_redis):
        chat_id, medicine_id, schedule_id = 555, 77, 7

        await scheduler_jobs_module.cancel_repeat_reminder(chat_id, medicine_id, schedule_id)

        called_key = mock_redis.delete.call_args[0][0]
        assert called_key == f"pending_reminder:{chat_id}:{medicine_id}:{schedule_id}"

    async def test_no_schedule_id_uses_the_sentinel_bucket(self, mock_redis):
        chat_id, medicine_id = 555, 77

        await scheduler_jobs_module.cancel_repeat_reminder(chat_id, medicine_id, None)

        called_key = mock_redis.delete.call_args[0][0]
        assert called_key == f"pending_reminder:{chat_id}:{medicine_id}:0"


class TestCancelRepeatRemindersForMedicine:
    async def test_cancels_every_dose_of_the_medicine_only(self, mock_redis, monkeypatch):
        chat_id, medicine_id = 111, 42

        async def _scan_iter(match=None):
            for key in [
                f"pending_reminder:{chat_id}:{medicine_id}:1",
                f"pending_reminder:{chat_id}:{medicine_id}:2",
                f"pending_reminder:{chat_id}:999:1",  # different medicine, must survive
            ]:
                yield key

        mock_redis.scan_iter = _scan_iter
        mock_redis.get = AsyncMock(return_value='{"medicine_name": "Aspirin"}')
        for job_id in (
            f"repeat_{medicine_id}_1_{chat_id}",
            f"repeat_{medicine_id}_2_{chat_id}",
            f"repeat_999_1_{chat_id}",
        ):
            scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id=job_id)

        await scheduler_jobs_module.cancel_repeat_reminders_for_medicine(chat_id, medicine_id)

        remaining_ids = {job.id for job in scheduler_jobs_module.scheduler.get_jobs()}
        assert f"repeat_{medicine_id}_1_{chat_id}" not in remaining_ids
        assert f"repeat_{medicine_id}_2_{chat_id}" not in remaining_ids
        assert f"repeat_999_1_{chat_id}" in remaining_ids


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


class TestLocalToday:
    def test_falls_back_to_kyiv_on_invalid_timezone(self):
        # Should not raise regardless of the bogus timezone string.
        result = scheduler_jobs_module._local_today("Not/A_Real_Timezone")
        from datetime import date

        assert isinstance(result, date)
