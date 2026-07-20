"""Tests for database/crud/medicines.py against a real (in-memory SQLite)
async session. See the `db_session` fixture in conftest.py.
"""

import database.crud as crud


class TestMedicines:
    async def test_add_medicine_creates_schedules(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")

        med = await crud.add_medicine(
            db_session,
            user_id=1,
            name="Aspirin",
            form="tablet",
            dosage="500mg",
            schedules_list=["08:00", "20:00"],
            course_duration=10,
        )

        assert med.name == "Aspirin"
        assert len(med.schedules) == 2
        assert {s.scheduled_time for s in med.schedules} == {"08:00", "20:00"}

    async def test_add_medicine_encrypts_name_at_rest(self, db_session):
        """
        The `name` column is EncryptedString. Reading it back through the ORM
        (which transparently decrypts) should show plaintext, but the raw
        value stored in the SQLite file must NOT be the plaintext string —
        this is the actual behavior worth verifying, not just that CRUD
        returns the right Python object.
        """
        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(
            db_session,
            user_id=1,
            name="VerySecretMedicineName",
            form="tablet",
            dosage="10mg",
            schedules_list=["08:00"],
            course_duration=5,
        )
        await db_session.commit()

        from sqlalchemy import text

        raw = await db_session.execute(text("SELECT name FROM medicines WHERE id = :id"), {"id": med.id})
        raw_value = raw.scalar_one()

        assert raw_value != "VerySecretMedicineName"
        assert med.name == "VerySecretMedicineName"

    async def test_get_user_medicines_active_only_by_default(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        active = await crud.add_medicine(
            db_session,
            1,
            "Active Med",
            "tablet",
            "1mg",
            ["08:00"],
            5,
        )
        inactive = await crud.add_medicine(
            db_session,
            1,
            "Inactive Med",
            "tablet",
            "1mg",
            ["08:00"],
            5,
        )
        await crud.update_medicine_field(db_session, inactive.id, "is_active", False)

        result = await crud.get_user_medicines(db_session, 1)
        assert {m.id for m in result} == {active.id}

    async def test_get_user_medicines_active_only_false_includes_archived(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(db_session, 1, "Med", "tablet", "1mg", ["08:00"], 5)
        await crud.update_medicine_field(db_session, med.id, "is_active", False)

        result = await crud.get_user_medicines(db_session, 1, active_only=False)
        assert len(result) == 1

    async def test_update_medicine_field_returns_false_for_missing(self, db_session):
        ok = await crud.update_medicine_field(db_session, 999, "dosage", "5mg")
        assert ok is False

    async def test_update_medicine_schedules_replaces_old_ones(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(db_session, 1, "Med", "tablet", "1mg", ["08:00"], 5)

        await crud.update_medicine_schedules(db_session, med.id, ["09:00", "21:00", "23:00"])

        updated = await crud.get_medicine_by_id(db_session, med.id)
        assert {s.scheduled_time for s in updated.schedules} == {"09:00", "21:00", "23:00"}

    async def test_delete_medicine_removes_it(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(db_session, 1, "Med", "tablet", "1mg", ["08:00"], 5)

        ok = await crud.delete_medicine(db_session, med.id)
        assert ok is True
        assert await crud.get_medicine_by_id(db_session, med.id) is None

    async def test_delete_medicine_nonexistent_returns_false(self, db_session):
        ok = await crud.delete_medicine(db_session, 999)
        assert ok is False


class TestRecordMedicineTaken:
    async def test_taken_decrements_course_duration_and_stock(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(
            db_session,
            1,
            "Med",
            "tablet",
            "1mg",
            ["08:00"],
            course_duration=5,
            stock_amount=10,
        )

        result = await crud.record_medicine_taken(db_session, med.id, status="taken")

        assert result["success"] is True
        assert result["remaining_days"] == 4
        assert result["stock_amount"] == 9

    async def test_skipped_does_not_decrement_stock(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(
            db_session,
            1,
            "Med",
            "tablet",
            "1mg",
            ["08:00"],
            course_duration=5,
            stock_amount=10,
        )

        result = await crud.record_medicine_taken(db_session, med.id, status="skipped")

        assert result["stock_amount"] == 10  # unchanged
        assert result["remaining_days"] == 5  # unchanged

    async def test_taken_does_not_go_below_zero_course_duration(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(
            db_session,
            1,
            "Med",
            "tablet",
            "1mg",
            ["08:00"],
            course_duration=0,
            stock_amount=5,
        )

        result = await crud.record_medicine_taken(db_session, med.id, status="taken")

        assert result["remaining_days"] == 0  # stays at 0, not negative

    async def test_taken_does_not_go_below_zero_stock(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(
            db_session,
            1,
            "Med",
            "tablet",
            "1mg",
            ["08:00"],
            course_duration=5,
            stock_amount=0,
        )

        result = await crud.record_medicine_taken(db_session, med.id, status="taken")

        assert result["stock_amount"] == 0  # stays at 0, not negative

    async def test_nonexistent_medicine_returns_failure(self, db_session):
        result = await crud.record_medicine_taken(db_session, 999, status="taken")
        assert result == {"success": False}

    async def test_stock_amount_none_is_left_as_none(self, db_session):
        # stock_amount is optional — a medicine tracked without stock shouldn't
        # suddenly get a numeric value from a "taken" event
        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(
            db_session,
            1,
            "Med",
            "tablet",
            "1mg",
            ["08:00"],
            course_duration=5,
            stock_amount=None,
        )

        result = await crud.record_medicine_taken(db_session, med.id, status="taken")

        assert result["stock_amount"] is None


class TestAddStock:
    async def test_adds_to_existing_stock(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(
            db_session,
            1,
            "Med",
            "tablet",
            "1mg",
            ["08:00"],
            course_duration=5,
            stock_amount=10,
        )

        new_stock = await crud.add_stock(db_session, med.id, 5)
        assert new_stock == 15

    async def test_adds_to_null_stock_treats_as_zero(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(
            db_session,
            1,
            "Med",
            "tablet",
            "1mg",
            ["08:00"],
            course_duration=5,
            stock_amount=None,
        )

        new_stock = await crud.add_stock(db_session, med.id, 7)
        assert new_stock == 7

    async def test_nonexistent_medicine_returns_none(self, db_session):
        result = await crud.add_stock(db_session, 999, 5)
        assert result is None


class TestArchivedMedicines:
    async def test_returns_only_inactive(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        await crud.add_medicine(db_session, 1, "Active", "tablet", "1mg", ["08:00"], 5)
        archived = await crud.add_medicine(db_session, 1, "Archived", "tablet", "1mg", ["08:00"], 5)
        await crud.update_medicine_field(db_session, archived.id, "is_active", False)

        result = await crud.get_archived_medicines(db_session, 1)
        assert {m.id for m in result} == {archived.id}
