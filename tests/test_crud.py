"""
Tests for database/crud.py against a real (in-memory SQLite) async session —
see the `db_session` fixture in conftest.py.
"""

from datetime import date, timedelta

import database.crud as crud


class TestUsers:
    async def test_get_or_create_user_creates_new(self, db_session):
        user = await crud.get_or_create_user(
            db_session,
            user_id=1,
            username="redi",
            full_name="Redi Test",
        )
        assert user.id == 1
        assert user.username == "redi"
        assert user.language == "ua"  # default

    async def test_get_or_create_user_returns_existing(self, db_session):
        first = await crud.get_or_create_user(db_session, 1, "redi", "Redi Test")
        second = await crud.get_or_create_user(db_session, 1, "someone_else", "Different Name")

        assert second.id == first.id
        # Should NOT overwrite the existing user's data with the new args
        assert second.username == "redi"

    async def test_get_all_users_returns_all(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        await crud.get_or_create_user(db_session, 2, "b", "B")

        users = await crud.get_all_users(db_session)
        assert {u.id for u in users} == {1, 2}

    async def test_update_user_timezone(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        await crud.update_user_timezone(db_session, 1, "America/New_York")

        tz = await crud.get_user_timezone(db_session, 1)
        assert tz == "America/New_York"

    async def test_update_user_timezone_nonexistent_user_no_error(self, db_session):
        # Should silently no-op rather than raising
        await crud.update_user_timezone(db_session, 999, "America/New_York")

    async def test_get_user_timezone_defaults_when_not_set(self, db_session):
        # A fresh user has a DB-level default of "Europe/Kyiv" set at insert time
        await crud.get_or_create_user(db_session, 1, "a", "A")
        tz = await crud.get_user_timezone(db_session, 1)
        assert tz == "Europe/Kyiv"

    async def test_get_user_language_defaults_for_unknown_user(self, db_session):
        # No such user at all -> falls back to "ua"
        lang = await crud.get_user_language(db_session, 999)
        assert lang == "ua"


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


class TestChatHistory:
    async def test_add_and_get_roundtrip(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        await crud.add_chat_message(db_session, 1, "user", "Hello there")
        await crud.add_chat_message(db_session, 1, "assistant", "Hi! How can I help?")

        history = await crud.get_chat_history(db_session, 1, limit=10)

        assert len(history) == 2
        # returned in chronological order (oldest first)
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Hello there"
        assert history[1]["role"] == "assistant"

    async def test_keep_last_trims_old_messages(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        for i in range(5):
            await crud.add_chat_message(db_session, 1, "user", f"msg {i}", keep_last=3)

        history = await crud.get_chat_history(db_session, 1, limit=10)
        assert len(history) == 3
        # the 3 most recent should be kept, in order
        assert [h["content"] for h in history] == ["msg 2", "msg 3", "msg 4"]

    async def test_clear_chat_history_removes_all(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        await crud.add_chat_message(db_session, 1, "user", "Hello")

        await crud.clear_chat_history(db_session, 1)

        history = await crud.get_chat_history(db_session, 1)
        assert history == []

    async def test_content_is_encrypted_at_rest(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        await crud.add_chat_message(db_session, 1, "user", "a very private message")
        await db_session.commit()

        from sqlalchemy import text

        raw = await db_session.execute(text("SELECT content FROM chat_history"))
        raw_value = raw.scalar_one()

        assert raw_value != "a very private message"


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


class TestMedicineIntakeStats:
    async def test_counts_taken_and_skipped(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(db_session, 1, "Med", "tablet", "1mg", ["08:00"], 5)

        await crud.record_medicine_taken(db_session, med.id, status="taken")
        await crud.record_medicine_taken(db_session, med.id, status="taken")
        await crud.record_medicine_taken(db_session, med.id, status="skipped")

        stats = await crud.get_medicine_intake_stats(db_session, 1)

        assert stats == {"total": 3, "taken": 2, "skipped": 1}

    async def test_no_records_returns_zeros(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        stats = await crud.get_medicine_intake_stats(db_session, 1)
        assert stats == {"total": 0, "taken": 0, "skipped": 0}
