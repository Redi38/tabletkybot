"""
Tests for services/scheduler/prescriptions.py::archive_expired_prescriptions
and ::check_prescription_reminders.

archive_expired_prescriptions had zero test coverage before, which is
exactly how it went unnoticed that it was never wired into any scheduler
job (see main.py — it's now registered as "presc_archive_expired_daily").
check_prescription_reminders had none either. These tests cover the
functions' own behavior; they don't test job registration itself.
"""

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

from database import crud
from services.scheduler import prescriptions as prescriptions_module
from services.scheduler.prescriptions import archive_expired_prescriptions, check_prescription_reminders


def _session_factory_for(db_session):
    """
    archive_expired_prescriptions expects a session_factory callable whose
    result is used as `async with session_factory() as session`, not a bare
    AsyncSession. Wrap the test's db_session fixture accordingly.
    """

    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


async def test_archives_expired_prescription_and_notifies_user(db_session, mock_bot):
    user = await crud.get_or_create_user(db_session, 1, "redi", "Redi Test")
    prescription = await crud.add_prescription(
        db_session,
        user_id=user.id,
        medicine_name="Amoxicillin",
        valid_from=date.today() - timedelta(days=30),
        expires_at=date.today() - timedelta(days=1),  # expired yesterday
    )
    await db_session.commit()

    await archive_expired_prescriptions(mock_bot, _session_factory_for(db_session))

    refreshed = await crud.get_prescription_by_id(db_session, prescription.id)
    assert refreshed.is_active is False
    mock_bot.send_message.assert_awaited_once()
    _, kwargs = mock_bot.send_message.call_args
    assert kwargs["chat_id"] == user.id


async def test_does_not_touch_still_valid_prescriptions(db_session, mock_bot):
    user = await crud.get_or_create_user(db_session, 1, "redi", "Redi Test")
    prescription = await crud.add_prescription(
        db_session,
        user_id=user.id,
        medicine_name="Ibuprofen",
        valid_from=date.today(),
        expires_at=date.today() + timedelta(days=30),  # not expired
    )
    await db_session.commit()

    await archive_expired_prescriptions(mock_bot, _session_factory_for(db_session))

    refreshed = await crud.get_prescription_by_id(db_session, prescription.id)
    assert refreshed.is_active is True
    mock_bot.send_message.assert_not_awaited()


async def test_does_not_reprocess_already_archived_prescription(db_session, mock_bot):
    user = await crud.get_or_create_user(db_session, 1, "redi", "Redi Test")
    prescription = await crud.add_prescription(
        db_session,
        user_id=user.id,
        medicine_name="Amoxicillin",
        valid_from=date.today() - timedelta(days=30),
        expires_at=date.today() - timedelta(days=1),
    )
    await crud.archive_prescription(db_session, prescription.id)
    await db_session.commit()

    await archive_expired_prescriptions(mock_bot, _session_factory_for(db_session))

    mock_bot.send_message.assert_not_awaited()


async def test_continues_after_a_notification_failure(db_session, mock_bot):
    """One user's send_message failing shouldn't stop the others from being archived."""
    user1 = await crud.get_or_create_user(db_session, 1, "user1", "User One")
    user2 = await crud.get_or_create_user(db_session, 2, "user2", "User Two")
    presc1 = await crud.add_prescription(
        db_session,
        user_id=user1.id,
        medicine_name="A",
        valid_from=date.today() - timedelta(days=30),
        expires_at=date.today() - timedelta(days=1),
    )
    presc2 = await crud.add_prescription(
        db_session,
        user_id=user2.id,
        medicine_name="B",
        valid_from=date.today() - timedelta(days=30),
        expires_at=date.today() - timedelta(days=1),
    )
    await db_session.commit()

    mock_bot.send_message.side_effect = [Exception("Telegram is down"), None]

    await archive_expired_prescriptions(mock_bot, _session_factory_for(db_session))

    refreshed1 = await crud.get_prescription_by_id(db_session, presc1.id)
    refreshed2 = await crud.get_prescription_by_id(db_session, presc2.id)
    assert refreshed1.is_active is False
    assert refreshed2.is_active is False


# ── check_prescription_reminders ──────────────────────────────────────
#
# The "is today the day, and is it 9am" check is timezone-sensitive, so
# these tests freeze services.scheduler.prescriptions.datetime.now() to a
# fixed instant (2026-07-20 09:00, naive) rather than depending on the
# real wall clock.


class _FrozenDateTime(datetime):
    fixed = datetime(2026, 7, 20, 9, 0)

    @classmethod
    def now(cls, tz=None):
        return cls.fixed.replace(tzinfo=tz) if tz else cls.fixed


def _freeze(monkeypatch, frozen=_FrozenDateTime):
    monkeypatch.setattr(prescriptions_module, "datetime", frozen)


async def test_reminder_sent_when_today_is_target_date_and_hour(db_session, mock_bot, monkeypatch):
    _freeze(monkeypatch)
    user = await crud.get_or_create_user(db_session, 1, "redi", "Redi Test")
    # target = expires_at - reminder_days_before = 2026-07-23 - 3 = 2026-07-20 (frozen "today")
    prescription = await crud.add_prescription(
        db_session,
        user_id=user.id,
        medicine_name="Amoxicillin",
        valid_from=date(2026, 1, 1),
        expires_at=date(2026, 7, 23),
        reminder_days_before=3,
    )
    await db_session.commit()

    await check_prescription_reminders(mock_bot, _session_factory_for(db_session))

    mock_bot.send_message.assert_awaited_once()
    assert mock_bot.send_message.call_args.kwargs["chat_id"] == user.id
    refreshed = await crud.get_prescription_by_id(db_session, prescription.id)
    assert refreshed.reminder_sent is True


async def test_no_reminder_when_target_date_not_reached(db_session, mock_bot, monkeypatch):
    _freeze(monkeypatch)
    user = await crud.get_or_create_user(db_session, 1, "redi", "Redi Test")
    await crud.add_prescription(
        db_session,
        user_id=user.id,
        medicine_name="Amoxicillin",
        valid_from=date(2026, 1, 1),
        expires_at=date(2026, 8, 1),  # target date is far in the future
        reminder_days_before=3,
    )
    await db_session.commit()

    await check_prescription_reminders(mock_bot, _session_factory_for(db_session))

    mock_bot.send_message.assert_not_awaited()


async def test_no_reminder_outside_the_9am_hour(db_session, mock_bot, monkeypatch):
    class _WrongHour(_FrozenDateTime):
        fixed = datetime(2026, 7, 20, 14, 0)

    _freeze(monkeypatch, _WrongHour)
    user = await crud.get_or_create_user(db_session, 1, "redi", "Redi Test")
    await crud.add_prescription(
        db_session,
        user_id=user.id,
        medicine_name="Amoxicillin",
        valid_from=date(2026, 1, 1),
        expires_at=date(2026, 7, 23),
        reminder_days_before=3,
    )
    await db_session.commit()

    await check_prescription_reminders(mock_bot, _session_factory_for(db_session))

    mock_bot.send_message.assert_not_awaited()


async def test_no_reminder_when_already_sent(db_session, mock_bot, monkeypatch):
    _freeze(monkeypatch)
    user = await crud.get_or_create_user(db_session, 1, "redi", "Redi Test")
    prescription = await crud.add_prescription(
        db_session,
        user_id=user.id,
        medicine_name="Amoxicillin",
        valid_from=date(2026, 1, 1),
        expires_at=date(2026, 7, 23),
        reminder_days_before=3,
    )
    await crud.mark_prescription_reminder_sent(db_session, prescription.id)
    await db_session.commit()

    await check_prescription_reminders(mock_bot, _session_factory_for(db_session))

    mock_bot.send_message.assert_not_awaited()


async def test_falls_back_to_kyiv_timezone_when_user_timezone_is_invalid(db_session, mock_bot, monkeypatch):
    _freeze(monkeypatch)
    user = await crud.get_or_create_user(db_session, 1, "redi", "Redi Test")
    await crud.update_user_timezone(db_session, user.id, "Not/AZone")
    await crud.add_prescription(
        db_session,
        user_id=user.id,
        medicine_name="Amoxicillin",
        valid_from=date(2026, 1, 1),
        expires_at=date(2026, 7, 23),
        reminder_days_before=3,
    )
    await db_session.commit()

    await check_prescription_reminders(mock_bot, _session_factory_for(db_session))

    mock_bot.send_message.assert_awaited_once()


async def test_reminder_message_uses_alert_keyboard_with_prescription_id(db_session, mock_bot, monkeypatch):
    _freeze(monkeypatch)
    user = await crud.get_or_create_user(db_session, 1, "redi", "Redi Test")
    prescription = await crud.add_prescription(
        db_session,
        user_id=user.id,
        medicine_name="Amoxicillin",
        valid_from=date(2026, 1, 1),
        expires_at=date(2026, 7, 23),
        reminder_days_before=3,
    )
    await db_session.commit()

    await check_prescription_reminders(mock_bot, _session_factory_for(db_session))

    keyboard = mock_bot.send_message.call_args.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].callback_data == f"presc_buy_ask_{prescription.id}"


async def test_does_not_mark_sent_when_send_message_fails(db_session, mock_bot, monkeypatch):
    _freeze(monkeypatch)
    user = await crud.get_or_create_user(db_session, 1, "redi", "Redi Test")
    prescription = await crud.add_prescription(
        db_session,
        user_id=user.id,
        medicine_name="Amoxicillin",
        valid_from=date(2026, 1, 1),
        expires_at=date(2026, 7, 23),
        reminder_days_before=3,
    )
    await db_session.commit()
    mock_bot.send_message.side_effect = Exception("Telegram is down")

    await check_prescription_reminders(mock_bot, _session_factory_for(db_session))  # should not raise

    refreshed = await crud.get_prescription_by_id(db_session, prescription.id)
    assert refreshed.reminder_sent is False


async def test_processes_multiple_prescriptions_independently(db_session, mock_bot, monkeypatch):
    _freeze(monkeypatch)
    user = await crud.get_or_create_user(db_session, 1, "redi", "Redi Test")
    matching = await crud.add_prescription(
        db_session,
        user_id=user.id,
        medicine_name="Matching",
        valid_from=date(2026, 1, 1),
        expires_at=date(2026, 7, 23),
        reminder_days_before=3,
    )
    await crud.add_prescription(
        db_session,
        user_id=user.id,
        medicine_name="NotMatching",
        valid_from=date(2026, 1, 1),
        expires_at=date(2026, 9, 1),
        reminder_days_before=3,
    )
    await db_session.commit()

    await check_prescription_reminders(mock_bot, _session_factory_for(db_session))

    mock_bot.send_message.assert_awaited_once()
    refreshed = await crud.get_prescription_by_id(db_session, matching.id)
    assert refreshed.reminder_sent is True
