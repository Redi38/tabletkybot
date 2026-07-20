"""
Tests for services/scheduler/prescriptions.py::archive_expired_prescriptions.

This function had zero test coverage before, which is exactly how it went
unnoticed that it was never wired into any scheduler job (see main.py —
it's now registered as "presc_archive_expired_daily"). These tests cover
the function's own behavior; they don't test the job registration itself.
"""

from contextlib import asynccontextmanager
from datetime import date, timedelta

from database import crud
from services.scheduler.prescriptions import archive_expired_prescriptions


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
