"""Tests for database/crud/prescriptions.py against a real (in-memory SQLite)
async session. See the `db_session` fixture in conftest.py.
"""

from datetime import date, timedelta

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
