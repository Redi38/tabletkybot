"""
Tests for services/scheduler/jobs/sync.py: adding per-medicine cron jobs
(idempotently), and the full/single reconciliation between the database
and the in-memory APScheduler job queue.
"""

from database.models import Medicine, MedicineSchedule
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


class TestAddRemindersForMedicineIdempotency:
    """
    Regression coverage for the O(n^2) fix: add_reminders_for_medicine used
    to rebuild `{job.id for job in scheduler.get_jobs()}` from scratch on
    every call, which meant sync_reminders() re-scanned the *entire* job
    list once per medicine. It's now a direct scheduler.get_job(job_id)
    lookup instead — these tests pin the observable behaviour (idempotent,
    still adds genuinely-new jobs, still skips inactive medicines).
    """

    def _medicine(self, medicine_id=1, schedule_times=("09:00",), is_active=True):
        medicine = Medicine(
            id=medicine_id,
            user_id=100,
            name="Ibuprofen",
            dosage="200mg",
            course_duration=5,
            is_active=is_active,
        )
        medicine.schedules = [
            MedicineSchedule(id=idx + 1, medicine_id=medicine_id, scheduled_time=t)
            for idx, t in enumerate(schedule_times)
        ]
        return medicine

    def test_creates_one_job_per_schedule(self, mock_redis, mock_bot):
        medicine = self._medicine(schedule_times=("09:00", "21:00"))

        scheduler_jobs_module.add_reminders_for_medicine(mock_bot, medicine, "Europe/Kyiv", chat_id=100)

        assert scheduler_jobs_module.scheduler.get_job("med_1_1") is not None
        assert scheduler_jobs_module.scheduler.get_job("med_1_2") is not None

    def test_second_call_is_a_no_op_for_already_scheduled_jobs(self, mock_redis, mock_bot):
        medicine = self._medicine(schedule_times=("09:00",))

        scheduler_jobs_module.add_reminders_for_medicine(mock_bot, medicine, "Europe/Kyiv", chat_id=100)
        jobs_after_first_call = {job.id for job in scheduler_jobs_module.scheduler.get_jobs()}

        # Calling again (as sync_reminders() does on every full sync) must not
        # duplicate or otherwise disturb the already-scheduled job.
        scheduler_jobs_module.add_reminders_for_medicine(mock_bot, medicine, "Europe/Kyiv", chat_id=100)
        jobs_after_second_call = {job.id for job in scheduler_jobs_module.scheduler.get_jobs()}

        assert jobs_after_second_call == jobs_after_first_call == {"med_1_1"}

    def test_adds_only_the_genuinely_new_schedule(self, mock_redis, mock_bot):
        medicine = self._medicine(schedule_times=("09:00",))
        scheduler_jobs_module.add_reminders_for_medicine(mock_bot, medicine, "Europe/Kyiv", chat_id=100)

        # A new schedule slot gets added to the same medicine (e.g. user added a dose)
        medicine.schedules.append(MedicineSchedule(id=2, medicine_id=1, scheduled_time="21:00"))
        scheduler_jobs_module.add_reminders_for_medicine(mock_bot, medicine, "Europe/Kyiv", chat_id=100)

        assert scheduler_jobs_module.scheduler.get_job("med_1_1") is not None
        assert scheduler_jobs_module.scheduler.get_job("med_1_2") is not None

    def test_inactive_medicine_removes_existing_reminders_instead(self, mock_redis, mock_bot, monkeypatch):
        monkeypatch.setattr("asyncio.create_task", lambda coro: coro.close())
        medicine = self._medicine(schedule_times=("09:00",), is_active=True)
        scheduler_jobs_module.add_reminders_for_medicine(mock_bot, medicine, "Europe/Kyiv", chat_id=100)
        assert scheduler_jobs_module.scheduler.get_job("med_1_1") is not None

        medicine.is_active = False
        scheduler_jobs_module.add_reminders_for_medicine(mock_bot, medicine, "Europe/Kyiv", chat_id=100)

        assert scheduler_jobs_module.scheduler.get_job("med_1_1") is None

    def test_survives_a_malformed_schedule_time(self, mock_redis, mock_bot):
        # An unparseable HH:MM raises inside CronTrigger construction; the
        # per-schedule loop must log it and move on rather than propagate.
        medicine = self._medicine(schedule_times=("not-a-time", "21:00"))

        scheduler_jobs_module.add_reminders_for_medicine(mock_bot, medicine, "Europe/Kyiv", chat_id=100)

        assert scheduler_jobs_module.scheduler.get_job("med_1_1") is None
        assert scheduler_jobs_module.scheduler.get_job("med_1_2") is not None


class TestSyncReminders:
    """
    Coverage for sync_reminders() — the full DB<->scheduler reconciliation
    that runs at startup and hourly. This is exactly the function whose
    per-medicine loop used to be O(medicines * total_jobs) (see
    TestAddRemindersForMedicineIdempotency above for the underlying fix);
    these tests pin its actual observable behaviour end-to-end.
    """

    async def _add_user_with_medicine(
        self, db_session, user_id, schedule_times=("09:00",), timezone=None, is_active=True
    ):
        from database import crud

        await crud.get_or_create_user(db_session, user_id, f"user{user_id}", f"User {user_id}")
        if timezone:
            await crud.update_user_timezone(db_session, user_id, timezone)
        medicine = await crud.add_medicine(
            db_session,
            user_id=user_id,
            name="Ibuprofen",
            form="tablets",
            dosage="200mg",
            schedules_list=list(schedule_times),
            course_duration=5,
        )
        if not is_active:
            await crud.update_medicine_field(db_session, medicine.id, "is_active", False)
        await db_session.commit()
        return medicine

    async def test_creates_jobs_for_all_active_medicines_across_users(self, mock_redis, mock_bot, db_session):
        med1 = await self._add_user_with_medicine(db_session, user_id=1, schedule_times=("09:00",))
        med2 = await self._add_user_with_medicine(db_session, user_id=2, schedule_times=("08:00", "20:00"))

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))

        job_ids = {job.id for job in scheduler_jobs_module.scheduler.get_jobs()}
        assert f"med_{med1.id}_{med1.schedules[0].id}" in job_ids
        assert f"med_{med2.id}_{med2.schedules[0].id}" in job_ids
        assert f"med_{med2.id}_{med2.schedules[1].id}" in job_ids

    async def test_skips_archived_medicines(self, mock_redis, mock_bot, db_session):
        await self._add_user_with_medicine(db_session, user_id=1, is_active=False)

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))

        assert scheduler_jobs_module.scheduler.get_jobs() == []

    async def test_removes_orphaned_med_jobs_no_longer_in_db(self, mock_redis, mock_bot, db_session, monkeypatch):
        monkeypatch.setattr("asyncio.create_task", lambda coro: coro.close())

        # A stale job left over from a medicine that was since deleted straight
        # from the DB (not through remove_reminders) — sync_reminders should
        # clean it up.
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id="med_999_1")

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))

        assert scheduler_jobs_module.scheduler.get_job("med_999_1") is None

    async def test_skips_medicines_belonging_to_a_blocked_user(self, mock_redis, mock_bot, db_session):
        from database import crud

        med = await self._add_user_with_medicine(db_session, user_id=1)
        await crud.mark_user_blocked(db_session, 1)
        await db_session.commit()

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))

        assert scheduler_jobs_module.scheduler.get_job(f"med_{med.id}_{med.schedules[0].id}") is None

    async def test_removes_a_stale_job_for_a_user_who_was_already_blocked(
        self, mock_redis, mock_bot, db_session, monkeypatch
    ):
        """
        Regression coverage: a user blocked before this cleanup existed (or
        while the bot was down) still has a leftover daily job in the
        in-memory scheduler from before — the next sync (startup or hourly)
        must remove it, not just skip adding new ones.
        """
        monkeypatch.setattr("asyncio.create_task", lambda coro: coro.close())
        from database import crud

        med = await self._add_user_with_medicine(db_session, user_id=1)
        job_id = f"med_{med.id}_{med.schedules[0].id}"
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id=job_id)
        await crud.mark_user_blocked(db_session, 1)
        await db_session.commit()

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))

        assert scheduler_jobs_module.scheduler.get_job(job_id) is None

    async def test_still_creates_jobs_for_non_blocked_users(self, mock_redis, mock_bot, db_session):
        med1 = await self._add_user_with_medicine(db_session, user_id=1)
        med2 = await self._add_user_with_medicine(db_session, user_id=2)
        from database import crud

        await crud.mark_user_blocked(db_session, 1)
        await db_session.commit()

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))

        assert scheduler_jobs_module.scheduler.get_job(f"med_{med1.id}_{med1.schedules[0].id}") is None
        assert scheduler_jobs_module.scheduler.get_job(f"med_{med2.id}_{med2.schedules[0].id}") is not None

    async def test_does_not_remove_unrelated_repeat_jobs(self, mock_redis, mock_bot, db_session):
        # Orphan cleanup only targets the "med_" prefix — an in-flight hourly
        # repeat job (unrelated lifecycle, cleaned up via cancel_repeat_reminder)
        # must survive a full sync untouched.
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id="repeat_1_100")

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))

        assert scheduler_jobs_module.scheduler.get_job("repeat_1_100") is not None

    async def test_falls_back_to_kyiv_timezone_when_user_has_none_set(self, mock_redis, mock_bot, db_session):
        await self._add_user_with_medicine(db_session, user_id=1, timezone=None)

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))

        job = next(iter(scheduler_jobs_module.scheduler.get_jobs()))
        assert str(job.trigger.timezone) == "Europe/Kyiv"

    async def test_uses_the_users_configured_timezone(self, mock_redis, mock_bot, db_session):
        await self._add_user_with_medicine(db_session, user_id=1, timezone="America/New_York")

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))

        job = next(iter(scheduler_jobs_module.scheduler.get_jobs()))
        assert str(job.trigger.timezone) == "America/New_York"

    async def test_running_sync_twice_is_idempotent(self, mock_redis, mock_bot, db_session):
        med = await self._add_user_with_medicine(db_session, user_id=1)

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))
        first_ids = {job.id for job in scheduler_jobs_module.scheduler.get_jobs()}

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))
        second_ids = {job.id for job in scheduler_jobs_module.scheduler.get_jobs()}

        assert first_ids == second_ids == {f"med_{med.id}_{med.schedules[0].id}"}

    async def test_no_medicines_at_all_leaves_scheduler_empty(self, mock_redis, mock_bot, db_session):
        from database import crud

        await crud.get_or_create_user(db_session, 1, "user1", "User 1")
        await db_session.commit()

        await scheduler_jobs_module.sync_reminders(mock_bot, _FakeSessionFactory(db_session))

        assert scheduler_jobs_module.scheduler.get_jobs() == []


class TestSyncSingleReminder:
    """
    Coverage for sync_single_reminder() — the point-signal handler used by
    the Admin Panel (add/edit/delete a medicine, or "send now").
    """

    async def _add_user_with_medicine(self, db_session, user_id=1, schedule_times=("09:00",)):
        from database import crud

        await crud.get_or_create_user(db_session, user_id, f"user{user_id}", f"User {user_id}")
        medicine = await crud.add_medicine(
            db_session,
            user_id=user_id,
            name="Ibuprofen",
            form="tablets",
            dosage="200mg",
            schedules_list=list(schedule_times),
            course_duration=5,
        )
        await db_session.commit()
        return medicine

    async def test_delete_action_removes_reminders_without_touching_db(
        self, mock_redis, mock_bot, db_session, monkeypatch
    ):
        monkeypatch.setattr("asyncio.create_task", lambda coro: coro.close())
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id="med_5_1")

        await scheduler_jobs_module.sync_single_reminder(mock_bot, _FakeSessionFactory(db_session), 5, "delete")

        assert scheduler_jobs_module.scheduler.get_job("med_5_1") is None

    async def test_edit_action_reschedules_the_medicine(self, mock_redis, mock_bot, db_session):
        medicine = await self._add_user_with_medicine(db_session)

        await scheduler_jobs_module.sync_single_reminder(mock_bot, _FakeSessionFactory(db_session), medicine.id, "edit")

        assert scheduler_jobs_module.scheduler.get_job(f"med_{medicine.id}_{medicine.schedules[0].id}") is not None

    async def test_send_now_action_sends_immediately_without_scheduling_a_cron_job(
        self, mock_redis, mock_bot, db_session
    ):
        medicine = await self._add_user_with_medicine(db_session)
        mock_bot.send_message.return_value.message_id = 123

        await scheduler_jobs_module.sync_single_reminder(
            mock_bot, _FakeSessionFactory(db_session), medicine.id, "send_now"
        )

        mock_bot.send_message.assert_awaited_once()
        # "send_now" is a one-off manual push, not a resync — no med_* cron job created
        assert scheduler_jobs_module.scheduler.get_job(f"med_{medicine.id}_{medicine.schedules[0].id}") is None

    async def test_medicine_missing_from_db_removes_any_leftover_reminders(
        self, mock_redis, mock_bot, db_session, monkeypatch
    ):
        monkeypatch.setattr("asyncio.create_task", lambda coro: coro.close())
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id="med_404_1")

        await scheduler_jobs_module.sync_single_reminder(mock_bot, _FakeSessionFactory(db_session), 404, "edit")

        assert scheduler_jobs_module.scheduler.get_job("med_404_1") is None
