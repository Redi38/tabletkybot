"""
Tests for database/crud/prescriptions.py — get_prescriptions_needing_reminder,
mark_prescription_reminder_sent, get_expired_active_prescriptions.
"""

from datetime import date, timedelta

import database.crud as crud

from ._helpers import _make_user


class TestReminderAndExpiryQueries:
    async def test_get_prescriptions_needing_reminder_excludes_sent(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        pending = await crud.add_prescription(
            db_session,
            1,
            "Pending",
            date(2026, 1, 1),
            date(2026, 1, 31),
        )
        already_sent = await crud.add_prescription(
            db_session,
            1,
            "Already Sent",
            date(2026, 1, 1),
            date(2026, 1, 31),
        )
        await crud.mark_prescription_reminder_sent(db_session, already_sent.id)

        result = await crud.get_prescriptions_needing_reminder(db_session)
        result_ids = {presc.id for presc, user in result}

        assert pending.id in result_ids
        assert already_sent.id not in result_ids

    async def test_get_prescriptions_needing_reminder_excludes_fully_purchased(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        presc = await crud.add_prescription(
            db_session,
            1,
            "Med",
            date(2026, 1, 1),
            date(2026, 1, 31),
            max_quantity=5,
        )
        await crud.mark_prescription_purchased(db_session, presc.id, 5)

        result = await crud.get_prescriptions_needing_reminder(db_session)
        assert presc.id not in {p.id for p, u in result}

    async def test_get_expired_active_prescriptions(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        expired = await crud.add_prescription(
            db_session,
            1,
            "Expired",
            date(2020, 1, 1),
            expires_at=date.today() - timedelta(days=1),
        )
        still_valid = await crud.add_prescription(
            db_session,
            1,
            "Valid",
            date.today(),
            expires_at=date.today() + timedelta(days=30),
        )

        result = await crud.get_expired_active_prescriptions(db_session)
        result_ids = {presc.id for presc, user in result}

        assert expired.id in result_ids
        assert still_valid.id not in result_ids


async def test_get_prescriptions_needing_reminder_returns_eligible_only(db_session):
    await _make_user(db_session, user_id=1)
    today = date.today()

    await crud.add_prescription(db_session, 1, "Eligible", today, today + timedelta(days=10))

    already_sent = await crud.add_prescription(db_session, 1, "Already sent", today, today + timedelta(days=10))
    await crud.mark_prescription_reminder_sent(db_session, already_sent.id)

    fully_purchased = await crud.add_prescription(
        db_session, 1, "Fully purchased", today, today + timedelta(days=10), max_quantity=5
    )
    await crud.mark_prescription_purchased(db_session, fully_purchased.id, 5)

    archived = await crud.add_prescription(db_session, 1, "Archived", today, today + timedelta(days=10))
    await crud.archive_prescription(db_session, archived.id)

    result = await crud.get_prescriptions_needing_reminder(db_session)

    names = [p.medicine_name for p, _ in result]
    assert names == ["Eligible"]


async def test_get_prescriptions_needing_reminder_includes_user_object(db_session):
    await _make_user(db_session, user_id=1)
    today = date.today()
    await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10))

    result = await crud.get_prescriptions_needing_reminder(db_session)

    assert len(result) == 1
    prescription, user = result[0]
    assert prescription.medicine_name == "Med"
    assert user.id == 1


async def test_mark_prescription_reminder_sent_excludes_from_future_queries(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10))

    await crud.mark_prescription_reminder_sent(db_session, created.id)

    result = await crud.get_prescriptions_needing_reminder(db_session)
    assert result == []


async def test_get_expired_active_prescriptions_returns_only_past_expiry(db_session):
    await _make_user(db_session, user_id=1)
    today = date.today()

    await crud.add_prescription(db_session, 1, "Expired", today - timedelta(days=30), today - timedelta(days=1))
    still_valid = await crud.add_prescription(db_session, 1, "Still valid", today, today + timedelta(days=10))

    result = await crud.get_expired_active_prescriptions(db_session)

    names = [p.medicine_name for p, _ in result]
    assert names == ["Expired"]
    assert still_valid.id not in [p.id for p, _ in result]


async def test_get_expired_active_prescriptions_excludes_already_archived(db_session):
    await _make_user(db_session)
    today = date.today()

    expired_and_archived = await crud.add_prescription(
        db_session, 1, "Expired archived", today - timedelta(days=30), today - timedelta(days=1)
    )
    await crud.archive_prescription(db_session, expired_and_archived.id)

    result = await crud.get_expired_active_prescriptions(db_session)

    assert result == []


async def test_get_expired_active_prescriptions_expiring_today_is_not_expired(db_session):
    """expires_at == today should NOT be treated as expired yet (strict < comparison)."""
    await _make_user(db_session)
    today = date.today()

    await crud.add_prescription(db_session, 1, "Expires today", today - timedelta(days=10), today)

    result = await crud.get_expired_active_prescriptions(db_session)

    assert result == []
