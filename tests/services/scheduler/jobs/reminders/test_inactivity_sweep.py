"""
Tests for services/scheduler/jobs/reminders/inactivity_sweep.py:
sweep_inactive_medicines() — the startup/periodic sweep that archives
medicines whose last recorded dose (or creation date, if none) is already
_MAX_UNACKNOWLEDGED_DAYS or more in the past. Unlike the day-to-day check in
send_reminder()/send_repeat_reminder(), this is what catches medicines that
were ALREADY stale before the feature was deployed.
"""

from datetime import datetime, timedelta, timezone

from services.scheduler import jobs as scheduler_jobs_module
from services.scheduler.jobs.reminders.utils import _MAX_UNACKNOWLEDGED_DAYS
from tests.services.scheduler.jobs.reminders._helpers import _FakeSessionFactory


async def _make_stale_medicine(db_session, days_old: int):
    from database import crud
    from database.models import Medicine

    user = await crud.get_or_create_user(db_session, 100, "tester", "Test User")
    await crud.update_user_language(db_session, 100, "en")
    medicine = Medicine(
        user_id=user.id,
        name="Ibuprofen",
        form="tablet",
        dosage="200mg",
        course_duration=5,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_old),
    )
    db_session.add(medicine)
    await db_session.commit()
    return medicine


class TestSweepInactiveMedicines:
    async def test_archives_a_medicine_already_stale_before_the_feature_existed(self, mock_redis, mock_bot, db_session):
        from database import crud

        medicine = await _make_stale_medicine(db_session, days_old=_MAX_UNACKNOWLEDGED_DAYS + 1)

        archived_count = await scheduler_jobs_module.sweep_inactive_medicines(mock_bot, _FakeSessionFactory(db_session))

        assert archived_count == 1
        refreshed = await crud.get_user_medicines(db_session, 100, active_only=False)
        assert refreshed[0].id == medicine.id
        assert refreshed[0].is_active is False
        mock_bot.send_message.assert_awaited_once()
        assert "archived" in mock_bot.send_message.call_args.kwargs["text"]

    async def test_leaves_a_recently_created_medicine_untouched(self, mock_redis, mock_bot, db_session):
        from database import crud

        await _make_stale_medicine(db_session, days_old=1)

        archived_count = await scheduler_jobs_module.sweep_inactive_medicines(mock_bot, _FakeSessionFactory(db_session))

        assert archived_count == 0
        refreshed = await crud.get_user_medicines(db_session, 100, active_only=False)
        assert refreshed[0].is_active is True
        mock_bot.send_message.assert_not_awaited()

    async def test_returns_zero_when_nothing_is_stale(self, mock_redis, mock_bot, db_session):
        from database import crud

        await crud.get_or_create_user(db_session, 100, "tester", "Test User")
        await crud.add_medicine(
            db_session,
            user_id=100,
            name="Ibuprofen",
            form="tablet",
            dosage="200mg",
            schedules_list=["09:00"],
            course_duration=5,
        )
        await db_session.commit()

        archived_count = await scheduler_jobs_module.sweep_inactive_medicines(mock_bot, _FakeSessionFactory(db_session))

        assert archived_count == 0
        mock_bot.send_message.assert_not_awaited()
