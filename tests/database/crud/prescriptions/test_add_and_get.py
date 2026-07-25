"""
Tests for database/crud/prescriptions.py — add_prescription,
get_user_prescriptions, get_prescription_by_id. Runs against a real
(in-memory SQLite) async session — see the `db_session` fixture in conftest.py.
"""

from datetime import date, timedelta

import database.crud as crud

from ._helpers import _make_user


class TestAddAndGetPrescriptions:
    async def test_add_and_get(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        presc = await crud.add_prescription(
            db_session,
            user_id=1,
            medicine_name="Amoxicillin",
            valid_from=date(2026, 1, 1),
            expires_at=date(2026, 1, 31),
            max_quantity=20,
        )

        result = await crud.get_user_prescriptions(db_session, 1)
        assert len(result) == 1
        assert result[0].medicine_name == "Amoxicillin"
        assert result[0].id == presc.id

    async def test_get_user_prescriptions_active_only_by_default(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        active = await crud.add_prescription(
            db_session,
            1,
            "Active Presc",
            date(2026, 1, 1),
            date(2026, 1, 31),
        )
        inactive = await crud.add_prescription(
            db_session,
            1,
            "Inactive Presc",
            date(2026, 1, 1),
            date(2026, 1, 31),
        )
        await crud.archive_prescription(db_session, inactive.id)

        result = await crud.get_user_prescriptions(db_session, 1)
        assert {p.id for p in result} == {active.id}


async def test_add_prescription_creates_record_with_defaults(db_session):
    await _make_user(db_session)
    today = date.today()
    expires = today + timedelta(days=30)

    prescription = await crud.add_prescription(
        db_session,
        user_id=1,
        medicine_name="Ibuprofen",
        valid_from=today,
        expires_at=expires,
    )

    assert prescription.id is not None
    assert prescription.medicine_name == "Ibuprofen"
    assert prescription.valid_from == today
    assert prescription.expires_at == expires
    assert prescription.max_quantity is None
    assert prescription.purchased_quantity == 0
    assert prescription.is_fully_purchased is False
    assert prescription.reminder_days_before == 3
    assert prescription.reminder_sent is False
    assert prescription.is_active is True


async def test_add_prescription_with_custom_quantity_and_reminder_days(db_session):
    await _make_user(db_session)
    today = date.today()

    prescription = await crud.add_prescription(
        db_session,
        user_id=1,
        medicine_name="Amoxicillin",
        valid_from=today,
        expires_at=today + timedelta(days=14),
        max_quantity=20,
        reminder_days_before=5,
    )

    assert prescription.max_quantity == 20
    assert prescription.reminder_days_before == 5


async def test_get_user_prescriptions_active_only_filters_archived(db_session):
    await _make_user(db_session)
    today = date.today()

    active = await crud.add_prescription(db_session, 1, "Active Med", today, today + timedelta(days=10))
    archived = await crud.add_prescription(db_session, 1, "Archived Med", today, today + timedelta(days=10))
    await crud.archive_prescription(db_session, archived.id)

    result = await crud.get_user_prescriptions(db_session, 1, active_only=True)

    names = [p.medicine_name for p in result]
    assert names == ["Active Med"]
    assert active.id in [p.id for p in result]


async def test_get_user_prescriptions_active_only_false_returns_all(db_session):
    await _make_user(db_session)
    today = date.today()

    p1 = await crud.add_prescription(db_session, 1, "Med A", today, today + timedelta(days=10))
    p2 = await crud.add_prescription(db_session, 1, "Med B", today, today + timedelta(days=5))
    await crud.archive_prescription(db_session, p2.id)

    result = await crud.get_user_prescriptions(db_session, 1, active_only=False)

    assert {p.id for p in result} == {p1.id, p2.id}


async def test_get_user_prescriptions_ordered_by_expiry(db_session):
    await _make_user(db_session)
    today = date.today()

    await crud.add_prescription(db_session, 1, "Later", today, today + timedelta(days=30))
    await crud.add_prescription(db_session, 1, "Sooner", today, today + timedelta(days=5))

    result = await crud.get_user_prescriptions(db_session, 1)

    assert [p.medicine_name for p in result] == ["Sooner", "Later"]


async def test_get_user_prescriptions_only_returns_requested_user(db_session):
    await _make_user(db_session, user_id=1)
    await _make_user(db_session, user_id=2)
    today = date.today()

    await crud.add_prescription(db_session, 1, "Mine", today, today + timedelta(days=10))
    await crud.add_prescription(db_session, 2, "Someone else's", today, today + timedelta(days=10))

    result = await crud.get_user_prescriptions(db_session, 1)

    assert len(result) == 1
    assert result[0].medicine_name == "Mine"


async def test_get_prescription_by_id_found_and_not_found(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10))

    found = await crud.get_prescription_by_id(db_session, created.id)
    missing = await crud.get_prescription_by_id(db_session, 999999)

    assert found is not None
    assert found.id == created.id
    assert missing is None
