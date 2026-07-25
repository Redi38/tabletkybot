"""
Tests for database/crud/prescriptions.py — archive_prescription,
delete_prescription, restore_prescription, get_user_archived_prescriptions.
"""

from datetime import date, timedelta

import database.crud as crud

from ._helpers import _make_user


class TestPrescriptionLifecycle:
    async def test_archive_prescription(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        presc = await crud.add_prescription(
            db_session,
            1,
            "Med",
            date(2026, 1, 1),
            date(2026, 1, 31),
        )

        ok = await crud.archive_prescription(db_session, presc.id)
        assert ok is True

        fetched = await crud.get_prescription_by_id(db_session, presc.id)
        assert fetched.is_active is False

    async def test_delete_prescription(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        presc = await crud.add_prescription(
            db_session,
            1,
            "Med",
            date(2026, 1, 1),
            date(2026, 1, 31),
        )

        ok = await crud.delete_prescription(db_session, presc.id)
        assert ok is True
        assert await crud.get_prescription_by_id(db_session, presc.id) is None

    async def test_get_user_archived_prescriptions(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        presc = await crud.add_prescription(
            db_session,
            1,
            "Med",
            date(2026, 1, 1),
            date(2026, 1, 31),
        )
        await crud.archive_prescription(db_session, presc.id)

        result = await crud.get_user_archived_prescriptions(db_session, 1)
        assert len(result) == 1
        assert result[0].id == presc.id

    async def test_restore_prescription_resets_purchase_state(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        presc = await crud.add_prescription(
            db_session,
            1,
            "Med",
            date(2026, 1, 1),
            date(2026, 1, 31),
            max_quantity=10,
        )
        await crud.mark_prescription_purchased(db_session, presc.id, 10)
        await crud.archive_prescription(db_session, presc.id)

        ok = await crud.restore_prescription(
            db_session,
            presc.id,
            valid_from=date(2026, 6, 1),
            expires_at=date(2026, 6, 30),
            max_quantity=20,
        )

        assert ok is True
        restored = await crud.get_prescription_by_id(db_session, presc.id)
        assert restored.is_active is True
        assert restored.purchased_quantity == 0
        assert restored.is_fully_purchased is False
        assert restored.reminder_sent is False
        assert restored.max_quantity == 20

    async def test_restore_prescription_nonexistent_returns_false(self, db_session):
        ok = await crud.restore_prescription(
            db_session,
            999,
            date(2026, 1, 1),
            date(2026, 1, 31),
            None,
        )
        assert ok is False


async def test_archive_prescription_sets_is_active_false(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10))

    success = await crud.archive_prescription(db_session, created.id)

    assert success is True
    updated = await crud.get_prescription_by_id(db_session, created.id)
    assert updated.is_active is False


async def test_delete_prescription_removes_it(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10))

    success = await crud.delete_prescription(db_session, created.id)

    assert success is True
    assert await crud.get_prescription_by_id(db_session, created.id) is None


async def test_delete_prescription_missing_id_returns_false(db_session):
    success = await crud.delete_prescription(db_session, 999999)
    assert success is False


async def test_restore_prescription_resets_all_purchase_and_status_fields(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10), max_quantity=10)
    await crud.mark_prescription_purchased(db_session, created.id, 10)  # fully purchased
    await crud.archive_prescription(db_session, created.id)  # and archived

    new_valid_from = today + timedelta(days=1)
    new_expires_at = today + timedelta(days=60)

    success = await crud.restore_prescription(
        db_session,
        created.id,
        valid_from=new_valid_from,
        expires_at=new_expires_at,
        max_quantity=20,
    )

    assert success is True
    restored = await crud.get_prescription_by_id(db_session, created.id)
    assert restored.valid_from == new_valid_from
    assert restored.expires_at == new_expires_at
    assert restored.max_quantity == 20
    assert restored.purchased_quantity == 0
    assert restored.is_fully_purchased is False
    assert restored.reminder_sent is False
    assert restored.is_active is True


async def test_restore_prescription_missing_id_returns_false(db_session):
    success = await crud.restore_prescription(
        db_session, 999999, valid_from=date.today(), expires_at=date.today(), max_quantity=None
    )
    assert success is False


async def test_get_user_archived_prescriptions_returns_only_archived_sorted_desc(db_session):
    await _make_user(db_session)
    today = date.today()

    active = await crud.add_prescription(db_session, 1, "Active", today, today + timedelta(days=10))

    archived_soon = await crud.add_prescription(db_session, 1, "Archived soon", today, today + timedelta(days=5))
    await crud.archive_prescription(db_session, archived_soon.id)

    archived_later = await crud.add_prescription(db_session, 1, "Archived later", today, today + timedelta(days=20))
    await crud.archive_prescription(db_session, archived_later.id)

    result = await crud.get_user_archived_prescriptions(db_session, 1)

    names = [p.medicine_name for p in result]
    assert names == ["Archived later", "Archived soon"]
    assert active.id not in [p.id for p in result]
