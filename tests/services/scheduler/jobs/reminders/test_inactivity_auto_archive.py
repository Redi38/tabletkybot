"""
Tests for the inactivity-based auto-archive branch added to send_reminder()
and send_repeat_reminder(): if a dose reminder has gone unacknowledged (no
Taken/Skip tap) for _MAX_UNACKNOWLEDGED_DAYS or more, the medicine is
archived and every scheduled job/Redis entry for it is cleared, instead of
the reminder (or its hourly repeat) being resent forever.
"""

import json
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import AsyncMock

from services.scheduler import jobs as scheduler_jobs_module
from services.scheduler.jobs.reminders.utils import _MAX_UNACKNOWLEDGED_DAYS
from tests.services.scheduler.jobs.reminders._helpers import _FakeSessionFactory


def _iso(days_ago: float) -> str:
    return (datetime.now(dt_timezone.utc) - timedelta(days=days_ago)).isoformat()


def _fake_get_factory(pending_json: str | None):
    """A redis.get side_effect that only returns pending-reminder data for
    pending_reminder:* keys, and None for everything else (in particular the
    stock-alert key), so the stock-alert auto-archive branch isn't tripped
    by accident."""

    async def _fake_get(key):
        if key.startswith("pending_reminder:"):
            return pending_json
        return None

    return _fake_get


async def _make_medicine(db_session):
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
    return medicine


class TestSendReminderInactivityAutoArchive:
    async def test_archives_when_unacknowledged_for_the_full_threshold(self, mock_redis, mock_bot, db_session):
        from database import crud

        medicine = await _make_medicine(db_session)
        old_pending = {
            "message_id": 1,
            "medicine_name": "Ibuprofen",
            "course_duration": 5,
            "language": "en",
            "timezone": "Europe/Kyiv",
            "sent_at": _iso(_MAX_UNACKNOWLEDGED_DAYS + 1),
            "first_sent_at": _iso(_MAX_UNACKNOWLEDGED_DAYS + 1),
        }
        mock_redis.get = AsyncMock(side_effect=_fake_get_factory(json.dumps(old_pending)))

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
        assert "reply_markup" not in mock_bot.send_message.call_args.kwargs
        assert "archived" in mock_bot.send_message.call_args.kwargs["text"]

    async def test_does_not_archive_before_the_threshold_and_keeps_the_original_first_sent_at(
        self, mock_redis, mock_bot, db_session
    ):
        from database import crud

        medicine = await _make_medicine(db_session)
        original_first_sent_at = _iso(2)
        old_pending = {
            "message_id": 1,
            "medicine_name": "Ibuprofen",
            "course_duration": 5,
            "language": "en",
            "timezone": "Europe/Kyiv",
            "sent_at": original_first_sent_at,
            "first_sent_at": original_first_sent_at,
        }
        mock_redis.get = AsyncMock(side_effect=_fake_get_factory(json.dumps(old_pending)))
        mock_bot.send_message.return_value.message_id = 555

        captured = {}

        async def _fake_set(key, value, ex=None):
            captured["value"] = value
            return True

        mock_redis.set = AsyncMock(side_effect=_fake_set)

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
        assert refreshed[0].is_active is True
        assert "reply_markup" in mock_bot.send_message.call_args.kwargs
        saved = json.loads(captured["value"])
        assert saved["first_sent_at"] == original_first_sent_at

    async def test_is_manual_bypasses_the_inactivity_check(self, mock_redis, mock_bot, db_session):
        from database import crud

        medicine = await _make_medicine(db_session)
        old_pending = {
            "message_id": 1,
            "medicine_name": "Ibuprofen",
            "course_duration": 5,
            "language": "en",
            "timezone": "Europe/Kyiv",
            "sent_at": _iso(_MAX_UNACKNOWLEDGED_DAYS + 1),
            "first_sent_at": _iso(_MAX_UNACKNOWLEDGED_DAYS + 1),
        }
        mock_redis.get = AsyncMock(side_effect=_fake_get_factory(json.dumps(old_pending)))
        mock_bot.send_message.return_value.message_id = 555

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

        refreshed = await crud.get_user_medicines(db_session, 100, active_only=False)
        assert refreshed[0].is_active is True
        assert "reply_markup" in mock_bot.send_message.call_args.kwargs


class TestSendRepeatReminderInactivityAutoArchive:
    async def test_archives_and_cancels_the_repeat_job_after_the_threshold(self, mock_redis, mock_bot, db_session):
        from database import crud

        medicine = await _make_medicine(db_session)
        old_pending = {
            "message_id": 1,
            "medicine_name": "Ibuprofen",
            "course_duration": 5,
            "language": "en",
            "timezone": "Europe/Kyiv",
            "sent_at": _iso(_MAX_UNACKNOWLEDGED_DAYS + 1),
            "first_sent_at": _iso(_MAX_UNACKNOWLEDGED_DAYS + 1),
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(old_pending))
        job_id = f"repeat_{medicine.id}_0_100"
        scheduler_jobs_module.scheduler.add_job(lambda: None, trigger="interval", hours=1, id=job_id)

        await scheduler_jobs_module.send_repeat_reminder(
            bot=mock_bot,
            medicine_id=medicine.id,
            chat_id=100,
            session_factory=_FakeSessionFactory(db_session),
        )

        assert scheduler_jobs_module.scheduler.get_job(job_id) is None
        refreshed = await crud.get_user_medicines(db_session, 100, active_only=False)
        assert refreshed[0].is_active is False
        # No delete-and-resend of the old reminder message — the medicine is
        # archived instead of nudging again.
        mock_bot.delete_message.assert_not_awaited()
        mock_bot.send_message.assert_awaited_once()
        assert "archived" in mock_bot.send_message.call_args.kwargs["text"]

    async def test_keeps_resending_and_preserves_first_sent_at_before_the_threshold(
        self, mock_redis, mock_bot, db_session
    ):
        medicine = await _make_medicine(db_session)
        original_first_sent_at = _iso(3)
        old_pending = {
            "message_id": 1,
            "medicine_name": "Ibuprofen",
            "course_duration": 5,
            "language": "en",
            "timezone": "Europe/Kyiv",
            "sent_at": original_first_sent_at,
            "first_sent_at": original_first_sent_at,
        }
        mock_redis.get = AsyncMock(return_value=json.dumps(old_pending))
        mock_bot.send_message.return_value.message_id = 777

        captured = {}

        async def _fake_set(key, value, ex=None):
            captured["value"] = value
            return True

        mock_redis.set = AsyncMock(side_effect=_fake_set)

        await scheduler_jobs_module.send_repeat_reminder(
            bot=mock_bot,
            medicine_id=medicine.id,
            chat_id=100,
            session_factory=_FakeSessionFactory(db_session),
        )

        mock_bot.delete_message.assert_awaited_once()
        mock_bot.send_message.assert_awaited_once()
        saved = json.loads(captured["value"])
        assert saved["first_sent_at"] == original_first_sent_at
