"""
Tests for services/scheduler/jobs/core.py: the shared APScheduler
instance, its idempotent start/stop lifecycle, and the
_next_schedule_id_for_today() helper used to pick which dose slot a
manual "send now" should target.
"""

from services.scheduler import jobs as scheduler_jobs_module
from services.scheduler.jobs import core as scheduler_jobs_core_module


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
            scheduler_jobs_core_module,
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
            scheduler_jobs_core_module,
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
            scheduler_jobs_core_module,
            "datetime",
            type("_DT", (), {"now": staticmethod(lambda tz=None: fixed_now)}),
        )

        schedules = [_FakeSchedule(1, "not-a-time"), _FakeSchedule(2, "12:00")]

        result = scheduler_jobs_module._next_schedule_id_for_today(schedules, "Europe/Kyiv")

        assert result == 2


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
