"""
Tests for database/crud/prescriptions.py — update_prescription_field and
mark_prescription_purchased.
"""

from datetime import date, timedelta

import database.crud as crud

from ._helpers import _make_user


class TestMarkPrescriptionPurchased:
    async def test_mark_prescription_purchased_accumulates(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        presc = await crud.add_prescription(
            db_session,
            1,
            "Med",
            date(2026, 1, 1),
            date(2026, 1, 31),
            max_quantity=10,
        )

        await crud.mark_prescription_purchased(db_session, presc.id, 4)
        result = await crud.mark_prescription_purchased(db_session, presc.id, 3)

        assert result["purchased_quantity"] == 7
        assert result["is_fully_purchased"] is False

    async def test_mark_prescription_purchased_flips_fully_purchased_flag(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        presc = await crud.add_prescription(
            db_session,
            1,
            "Med",
            date(2026, 1, 1),
            date(2026, 1, 31),
            max_quantity=10,
        )

        result = await crud.mark_prescription_purchased(db_session, presc.id, 10)

        assert result["is_fully_purchased"] is True

    async def test_mark_prescription_purchased_nonexistent_returns_failure(self, db_session):
        result = await crud.mark_prescription_purchased(db_session, 999, 5)
        assert result == {"success": False}


async def test_update_prescription_field_updates_value(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10))

    success = await crud.update_prescription_field(db_session, created.id, "max_quantity", 42)

    assert success is True
    updated = await crud.get_prescription_by_id(db_session, created.id)
    assert updated.max_quantity == 42


async def test_update_prescription_field_returns_false_for_missing_id(db_session):
    success = await crud.update_prescription_field(db_session, 999999, "max_quantity", 1)
    assert success is False


async def test_mark_prescription_purchased_partial_purchase(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10), max_quantity=10)

    result = await crud.mark_prescription_purchased(db_session, created.id, 4)

    assert result["success"] is True
    assert result["purchased_quantity"] == 4
    assert result["max_quantity"] == 10
    assert result["is_fully_purchased"] is False


async def test_mark_prescription_purchased_accumulates_across_calls(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10), max_quantity=10)

    await crud.mark_prescription_purchased(db_session, created.id, 3)
    result = await crud.mark_prescription_purchased(db_session, created.id, 4)

    assert result["purchased_quantity"] == 7
    assert result["is_fully_purchased"] is False


async def test_mark_prescription_purchased_reaches_max_sets_fully_purchased(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10), max_quantity=10)

    result = await crud.mark_prescription_purchased(db_session, created.id, 10)

    assert result["is_fully_purchased"] is True


async def test_mark_prescription_purchased_can_exceed_max_and_still_fully_purchased(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10), max_quantity=10)

    result = await crud.mark_prescription_purchased(db_session, created.id, 15)

    assert result["purchased_quantity"] == 15
    assert result["is_fully_purchased"] is True


async def test_mark_prescription_purchased_no_max_quantity_never_fully_purchased(db_session):
    """max_quantity is None ('unlimited'/'unspecified') -> should never auto-complete."""
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10))

    result = await crud.mark_prescription_purchased(db_session, created.id, 1000)

    assert result["max_quantity"] is None
    assert result["is_fully_purchased"] is False


async def test_mark_prescription_purchased_missing_id_returns_failure(db_session):
    result = await crud.mark_prescription_purchased(db_session, 999999, 5)
    assert result == {"success": False}
