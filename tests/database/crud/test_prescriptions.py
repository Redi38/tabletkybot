"""Tests for database/crud/prescriptions.py against a real (in-memory SQLite)
async session. See the `db_session` fixture in conftest.py.
"""

from datetime import date, timedelta

import pytest

import database.crud as crud


class TestPrescriptions:
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


# ─── Additional coverage merged from the old standalone test_prescriptions.py ──
# (free functions, not part of TestPrescriptions — kept as-is to avoid
# renaming/restructuring tests that were already passing)


async def _make_user(db_session, user_id: int = 1) -> None:
    await crud.get_or_create_user(db_session, user_id, "redi", "Redi Test")


# add_prescription / get_user_prescriptions


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_get_user_prescriptions_active_only_false_returns_all(db_session):
    await _make_user(db_session)
    today = date.today()

    p1 = await crud.add_prescription(db_session, 1, "Med A", today, today + timedelta(days=10))
    p2 = await crud.add_prescription(db_session, 1, "Med B", today, today + timedelta(days=5))
    await crud.archive_prescription(db_session, p2.id)

    result = await crud.get_user_prescriptions(db_session, 1, active_only=False)

    assert {p.id for p in result} == {p1.id, p2.id}


@pytest.mark.asyncio
async def test_get_user_prescriptions_ordered_by_expiry(db_session):
    await _make_user(db_session)
    today = date.today()

    await crud.add_prescription(db_session, 1, "Later", today, today + timedelta(days=30))
    await crud.add_prescription(db_session, 1, "Sooner", today, today + timedelta(days=5))

    result = await crud.get_user_prescriptions(db_session, 1)

    assert [p.medicine_name for p in result] == ["Sooner", "Later"]


@pytest.mark.asyncio
async def test_get_user_prescriptions_only_returns_requested_user(db_session):
    await _make_user(db_session, user_id=1)
    await _make_user(db_session, user_id=2)
    today = date.today()

    await crud.add_prescription(db_session, 1, "Mine", today, today + timedelta(days=10))
    await crud.add_prescription(db_session, 2, "Someone else's", today, today + timedelta(days=10))

    result = await crud.get_user_prescriptions(db_session, 1)

    assert len(result) == 1
    assert result[0].medicine_name == "Mine"


# get_prescription_by_id / update_prescription_field


@pytest.mark.asyncio
async def test_get_prescription_by_id_found_and_not_found(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10))

    found = await crud.get_prescription_by_id(db_session, created.id)
    missing = await crud.get_prescription_by_id(db_session, 999999)

    assert found is not None
    assert found.id == created.id
    assert missing is None


@pytest.mark.asyncio
async def test_update_prescription_field_updates_value(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10))

    success = await crud.update_prescription_field(db_session, created.id, "max_quantity", 42)

    assert success is True
    updated = await crud.get_prescription_by_id(db_session, created.id)
    assert updated.max_quantity == 42


@pytest.mark.asyncio
async def test_update_prescription_field_returns_false_for_missing_id(db_session):
    success = await crud.update_prescription_field(db_session, 999999, "max_quantity", 1)
    assert success is False


# mark_prescription_purchased


@pytest.mark.asyncio
async def test_mark_prescription_purchased_partial_purchase(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10), max_quantity=10)

    result = await crud.mark_prescription_purchased(db_session, created.id, 4)

    assert result["success"] is True
    assert result["purchased_quantity"] == 4
    assert result["max_quantity"] == 10
    assert result["is_fully_purchased"] is False


@pytest.mark.asyncio
async def test_mark_prescription_purchased_accumulates_across_calls(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10), max_quantity=10)

    await crud.mark_prescription_purchased(db_session, created.id, 3)
    result = await crud.mark_prescription_purchased(db_session, created.id, 4)

    assert result["purchased_quantity"] == 7
    assert result["is_fully_purchased"] is False


@pytest.mark.asyncio
async def test_mark_prescription_purchased_reaches_max_sets_fully_purchased(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10), max_quantity=10)

    result = await crud.mark_prescription_purchased(db_session, created.id, 10)

    assert result["is_fully_purchased"] is True


@pytest.mark.asyncio
async def test_mark_prescription_purchased_can_exceed_max_and_still_fully_purchased(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10), max_quantity=10)

    result = await crud.mark_prescription_purchased(db_session, created.id, 15)

    assert result["purchased_quantity"] == 15
    assert result["is_fully_purchased"] is True


@pytest.mark.asyncio
async def test_mark_prescription_purchased_no_max_quantity_never_fully_purchased(db_session):
    """max_quantity is None ('unlimited'/'unspecified') -> should never auto-complete."""
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10))

    result = await crud.mark_prescription_purchased(db_session, created.id, 1000)

    assert result["max_quantity"] is None
    assert result["is_fully_purchased"] is False


@pytest.mark.asyncio
async def test_mark_prescription_purchased_missing_id_returns_failure(db_session):
    result = await crud.mark_prescription_purchased(db_session, 999999, 5)
    assert result == {"success": False}


# archive_prescription / delete_prescription / restore_prescription


@pytest.mark.asyncio
async def test_archive_prescription_sets_is_active_false(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10))

    success = await crud.archive_prescription(db_session, created.id)

    assert success is True
    updated = await crud.get_prescription_by_id(db_session, created.id)
    assert updated.is_active is False


@pytest.mark.asyncio
async def test_delete_prescription_removes_it(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10))

    success = await crud.delete_prescription(db_session, created.id)

    assert success is True
    assert await crud.get_prescription_by_id(db_session, created.id) is None


@pytest.mark.asyncio
async def test_delete_prescription_missing_id_returns_false(db_session):
    success = await crud.delete_prescription(db_session, 999999)
    assert success is False


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_restore_prescription_missing_id_returns_false(db_session):
    success = await crud.restore_prescription(
        db_session, 999999, valid_from=date.today(), expires_at=date.today(), max_quantity=None
    )
    assert success is False


# get_prescriptions_needing_reminder


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_get_prescriptions_needing_reminder_includes_user_object(db_session):
    await _make_user(db_session, user_id=1)
    today = date.today()
    await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10))

    result = await crud.get_prescriptions_needing_reminder(db_session)

    assert len(result) == 1
    prescription, user = result[0]
    assert prescription.medicine_name == "Med"
    assert user.id == 1


@pytest.mark.asyncio
async def test_mark_prescription_reminder_sent_excludes_from_future_queries(db_session):
    await _make_user(db_session)
    today = date.today()
    created = await crud.add_prescription(db_session, 1, "Med", today, today + timedelta(days=10))

    await crud.mark_prescription_reminder_sent(db_session, created.id)

    result = await crud.get_prescriptions_needing_reminder(db_session)
    assert result == []


# get_expired_active_prescriptions


@pytest.mark.asyncio
async def test_get_expired_active_prescriptions_returns_only_past_expiry(db_session):
    await _make_user(db_session, user_id=1)
    today = date.today()

    await crud.add_prescription(db_session, 1, "Expired", today - timedelta(days=30), today - timedelta(days=1))
    still_valid = await crud.add_prescription(db_session, 1, "Still valid", today, today + timedelta(days=10))

    result = await crud.get_expired_active_prescriptions(db_session)

    names = [p.medicine_name for p, _ in result]
    assert names == ["Expired"]
    assert still_valid.id not in [p.id for p, _ in result]


@pytest.mark.asyncio
async def test_get_expired_active_prescriptions_excludes_already_archived(db_session):
    await _make_user(db_session)
    today = date.today()

    expired_and_archived = await crud.add_prescription(
        db_session, 1, "Expired archived", today - timedelta(days=30), today - timedelta(days=1)
    )
    await crud.archive_prescription(db_session, expired_and_archived.id)

    result = await crud.get_expired_active_prescriptions(db_session)

    assert result == []


@pytest.mark.asyncio
async def test_get_expired_active_prescriptions_expiring_today_is_not_expired(db_session):
    """expires_at == today should NOT be treated as expired yet (strict < comparison)."""
    await _make_user(db_session)
    today = date.today()

    await crud.add_prescription(db_session, 1, "Expires today", today - timedelta(days=10), today)

    result = await crud.get_expired_active_prescriptions(db_session)

    assert result == []


# get_user_archived_prescriptions


@pytest.mark.asyncio
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
