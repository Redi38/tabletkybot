"""Tests for database/crud/stats.py against a real (in-memory SQLite)
async session. See the `db_session` fixture in conftest.py.
"""

import database.crud as crud


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
