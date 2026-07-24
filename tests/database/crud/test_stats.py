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

    async def test_scoped_to_the_requesting_user_only(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        await crud.get_or_create_user(db_session, 2, "b", "B")
        med1 = await crud.add_medicine(db_session, 1, "Med1", "tablet", "1mg", ["08:00"], 5)
        med2 = await crud.add_medicine(db_session, 2, "Med2", "tablet", "1mg", ["08:00"], 5)

        await crud.record_medicine_taken(db_session, med1.id, status="taken")
        await crud.record_medicine_taken(db_session, med2.id, status="taken")
        await crud.record_medicine_taken(db_session, med2.id, status="taken")

        stats = await crud.get_medicine_intake_stats(db_session, 1)

        assert stats["total"] == 1


class TestGetMedicineRecordsForReport:
    async def test_returns_empty_list_with_no_records(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")

        records = await crud.get_medicine_records_for_report(db_session, 1)

        assert records == []

    async def test_returns_name_dosage_and_status_ordered_by_time(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(db_session, 1, "Ibuprofen", "tablet", "200mg", ["08:00"], 5)

        await crud.record_medicine_taken(db_session, med.id, status="taken")
        await crud.record_medicine_taken(db_session, med.id, status="skipped")

        records = await crud.get_medicine_records_for_report(db_session, 1)

        assert len(records) == 2
        assert records[0][0] == "Ibuprofen"
        assert records[0][1] == "200mg"
        assert {r[4] for r in records} == {"taken", "skipped"}

    async def test_only_includes_the_requesting_users_records(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        await crud.get_or_create_user(db_session, 2, "b", "B")
        med1 = await crud.add_medicine(db_session, 1, "Mine", "tablet", "1mg", ["08:00"], 5)
        med2 = await crud.add_medicine(db_session, 2, "Someone Else's", "tablet", "1mg", ["08:00"], 5)

        await crud.record_medicine_taken(db_session, med1.id, status="taken")
        await crud.record_medicine_taken(db_session, med2.id, status="taken")

        records = await crud.get_medicine_records_for_report(db_session, 1)

        assert len(records) == 1
        assert records[0][0] == "Mine"


class TestGetGlobalIntakeStats:
    async def test_returns_zero_adherence_with_no_data(self, db_session):
        stats = await crud.get_global_intake_stats(db_session)

        assert stats["taken"] == 0
        assert stats["skipped"] == 0
        assert stats["adherence_rate"] == 0.0
        assert stats["total_users"] == 0
        assert stats["total_active_medicines"] == 0
        assert stats["active_prescriptions"] == 0

    async def test_computes_adherence_rate_across_all_users(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        await crud.get_or_create_user(db_session, 2, "b", "B")
        med1 = await crud.add_medicine(db_session, 1, "Med1", "tablet", "1mg", ["08:00"], 5)
        med2 = await crud.add_medicine(db_session, 2, "Med2", "tablet", "1mg", ["08:00"], 5)

        await crud.record_medicine_taken(db_session, med1.id, status="taken")
        await crud.record_medicine_taken(db_session, med1.id, status="taken")
        await crud.record_medicine_taken(db_session, med1.id, status="taken")
        await crud.record_medicine_taken(db_session, med2.id, status="skipped")

        stats = await crud.get_global_intake_stats(db_session)

        assert stats["taken"] == 3
        assert stats["skipped"] == 1
        assert stats["adherence_rate"] == 75.0
        assert stats["total_users"] == 2
        assert stats["total_active_medicines"] == 2

    async def test_archived_medicines_are_excluded_from_active_count(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(db_session, 1, "Med", "tablet", "1mg", ["08:00"], 5)
        await crud.update_medicine_field(db_session, med.id, "is_active", False)
        await db_session.commit()

        stats = await crud.get_global_intake_stats(db_session)

        assert stats["total_active_medicines"] == 0


class TestGetDashboardStats:
    async def test_no_period_filter_includes_everything(self, db_session):
        from datetime import datetime, timedelta, timezone

        from database.models import MedicineRecord

        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(db_session, 1, "Med", "tablet", "1mg", ["08:00"], 5)
        old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=100)
        db_session.add(MedicineRecord(medicine_id=med.id, status="taken", taken_at=old))
        await db_session.commit()

        result = await crud.get_dashboard_stats(db_session, period="all")

        assert result["pie"]["taken"] == 1

    async def test_24h_period_excludes_older_records(self, db_session):
        from datetime import datetime, timedelta, timezone

        from database.models import MedicineRecord

        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(db_session, 1, "Med", "tablet", "1mg", ["08:00"], 5)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        db_session.add(MedicineRecord(medicine_id=med.id, status="taken", taken_at=now - timedelta(hours=1)))
        db_session.add(MedicineRecord(medicine_id=med.id, status="taken", taken_at=now - timedelta(days=2)))
        await db_session.commit()

        result = await crud.get_dashboard_stats(db_session, period="24h")

        assert result["pie"]["taken"] == 1

    async def test_7d_and_30d_periods_are_both_recognized(self, db_session):
        from datetime import datetime, timedelta, timezone

        from database.models import MedicineRecord

        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(db_session, 1, "Med", "tablet", "1mg", ["08:00"], 5)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        db_session.add(MedicineRecord(medicine_id=med.id, status="taken", taken_at=now - timedelta(days=5)))
        await db_session.commit()

        result_7d = await crud.get_dashboard_stats(db_session, period="7d")
        result_30d = await crud.get_dashboard_stats(db_session, period="30d")

        assert result_7d["pie"]["taken"] == 1
        assert result_30d["pie"]["taken"] == 1

    async def test_unknown_period_falls_back_to_no_filter(self, db_session):
        from datetime import datetime, timedelta, timezone

        from database.models import MedicineRecord

        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(db_session, 1, "Med", "tablet", "1mg", ["08:00"], 5)
        old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1000)
        db_session.add(MedicineRecord(medicine_id=med.id, status="taken", taken_at=old))
        await db_session.commit()

        result = await crud.get_dashboard_stats(db_session, period="not-a-real-period")

        assert result["pie"]["taken"] == 1

    async def test_hourly_breakdown_only_counts_taken_doses_at_the_right_hour(self, db_session):
        from datetime import datetime

        from database.models import MedicineRecord

        await crud.get_or_create_user(db_session, 1, "a", "A")
        med = await crud.add_medicine(db_session, 1, "Med", "tablet", "1mg", ["08:00"], 5)
        db_session.add(MedicineRecord(medicine_id=med.id, status="taken", taken_at=datetime(2026, 1, 1, 14, 30)))
        db_session.add(MedicineRecord(medicine_id=med.id, status="taken", taken_at=datetime(2026, 1, 1, 14, 45)))
        db_session.add(MedicineRecord(medicine_id=med.id, status="skipped", taken_at=datetime(2026, 1, 1, 9, 0)))
        await db_session.commit()

        result = await crud.get_dashboard_stats(db_session, period="all")

        assert result["hourly"][14] == 2
        assert result["hourly"][9] == 0  # skipped doses aren't counted in the hourly chart
        assert sum(result["hourly"]) == 2

    async def test_hourly_array_has_24_slots_defaulting_to_zero(self, db_session):
        await crud.get_or_create_user(db_session, 1, "a", "A")

        result = await crud.get_dashboard_stats(db_session, period="all")

        assert len(result["hourly"]) == 24
        assert all(count == 0 for count in result["hourly"])
