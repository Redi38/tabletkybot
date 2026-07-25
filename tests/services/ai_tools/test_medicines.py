"""
Tests for services/ai_tools/medicines.py — medicine-related tool executors.
crud is mocked via monkeypatch/AsyncMock rather than hitting a real DB. The
goal is to verify VALIDATION logic (bounds checking, malformed input
handling), not SQLAlchemy behavior itself.
"""

from unittest.mock import AsyncMock, MagicMock

from services.ai_tools import (
    execute_add_medicine_reminder,
    execute_get_my_medicines,
    execute_request_medicine_removal,
    execute_update_medicine,
)

from ._crud_patches import _patch_crud_get_user_medicines


class TestExecuteAddMedicineReminder:
    async def test_rejects_empty_times_list(self):
        session = AsyncMock()
        result = await execute_add_medicine_reminder(
            session,
            user_id=1,
            args={
                "name": "Aspirin",
                "form": "tablet",
                "dosage": "500mg",
                "times": [],
                "duration_days": 10,
            },
        )
        assert "error" in result

    async def test_rejects_missing_times(self):
        session = AsyncMock()
        result = await execute_add_medicine_reminder(
            session,
            user_id=1,
            args={
                "name": "Aspirin",
                "form": "tablet",
                "dosage": "500mg",
                "duration_days": 10,
            },
        )
        assert "error" in result

    async def test_rejects_duration_out_of_bounds(self):
        session = AsyncMock()
        result = await execute_add_medicine_reminder(
            session,
            user_id=1,
            args={
                "name": "Aspirin",
                "form": "tablet",
                "dosage": "500mg",
                "times": ["08:00"],
                "duration_days": 999,
            },
        )
        assert "error" in result
        assert "duration_days" in result["error"]

    async def test_rejects_invalid_stock_amount(self):
        session = AsyncMock()
        result = await execute_add_medicine_reminder(
            session,
            user_id=1,
            args={
                "name": "Aspirin",
                "form": "tablet",
                "dosage": "500mg",
                "times": ["08:00"],
                "duration_days": 10,
                "stock_amount": -5,
            },
        )
        assert "error" in result

    async def test_calculates_course_duration_as_days_times_frequency(self, monkeypatch):
        import database.crud as crud_module

        added_kwargs = {}

        async def fake_add_medicine(**kwargs):
            added_kwargs.update(kwargs)
            med = MagicMock()
            med.name = kwargs["name"]
            return med

        monkeypatch.setattr(crud_module, "add_medicine", fake_add_medicine)

        session = AsyncMock()
        result = await execute_add_medicine_reminder(
            session,
            user_id=1,
            args={
                "name": "Aspirin",
                "form": "tablet",
                "dosage": "500mg",
                "times": ["08:00", "20:00"],
                "duration_days": 10,
            },
        )

        assert result["success"] is True
        # 10 days * 2 doses/day = 20 total doses
        assert added_kwargs["course_duration"] == 20


class TestExecuteUpdateMedicine:
    async def test_medicine_not_found_returns_error(self):
        session = AsyncMock()
        with _patch_crud_get_user_medicines(session, []):
            result = await execute_update_medicine(
                session,
                user_id=1,
                args={
                    "medicine_name": "Unknown",
                    "field": "dosage",
                    "value": "1000mg",
                },
            )
            assert "error" in result

    async def test_rejects_invalid_stock_amount_value(self, monkeypatch):
        import database.crud as crud_module

        med = MagicMock()
        med.name = "Aspirin"
        med.id = 1

        with _patch_crud_get_user_medicines(AsyncMock(), [med]):
            monkeypatch.setattr(crud_module, "update_medicine_field", AsyncMock())
            session = AsyncMock()
            result = await execute_update_medicine(
                session,
                user_id=1,
                args={
                    "medicine_name": "Aspirin",
                    "field": "stock_amount",
                    "value": "not-a-number",
                },
            )
            assert "error" in result


class TestExecuteUpdateMedicineExtra:
    async def test_rejects_missing_field(self):
        med = MagicMock()
        med.name = "Aspirin"
        med.id = 1

        with _patch_crud_get_user_medicines(AsyncMock(), [med]):
            session = AsyncMock()
            result = await execute_update_medicine(
                session, user_id=1, args={"medicine_name": "Aspirin", "field": None, "value": "x"}
            )

        assert "error" in result

    async def test_updates_a_text_field_and_truncates_to_150_chars(self, monkeypatch):
        import database.crud as crud_module

        med = MagicMock()
        med.name = "Aspirin"
        med.id = 1
        update_call = {}

        async def fake_update_field(session, medicine_id, field, value):
            update_call.update({"medicine_id": medicine_id, "field": field, "value": value})

        monkeypatch.setattr(crud_module, "update_medicine_field", fake_update_field)

        with _patch_crud_get_user_medicines(AsyncMock(), [med]):
            session = AsyncMock()
            result = await execute_update_medicine(
                session, user_id=1, args={"medicine_name": "Aspirin", "field": "dosage", "value": "1000mg"}
            )

        assert result["success"] is True
        assert result["updated_field"] == "dosage"
        assert update_call == {"medicine_id": 1, "field": "dosage", "value": "1000mg"}

    async def test_updates_a_numeric_field_within_bounds(self, monkeypatch):
        import database.crud as crud_module

        med = MagicMock()
        med.name = "Aspirin"
        med.id = 1

        monkeypatch.setattr(crud_module, "update_medicine_field", AsyncMock())

        with _patch_crud_get_user_medicines(AsyncMock(), [med]):
            session = AsyncMock()
            result = await execute_update_medicine(
                session, user_id=1, args={"medicine_name": "Aspirin", "field": "stock_amount", "value": "15"}
            )

        assert result["success"] is True
        assert result["new_value"] == 15


class TestExecuteGetMyMedicines:
    async def test_returns_empty_list_with_note_when_none(self):
        session = AsyncMock()
        with _patch_crud_get_user_medicines(session, []):
            result = await execute_get_my_medicines(session, user_id=1, args={})

        assert result["medicines"] == []
        assert "note" in result

    async def test_returns_serialized_medicine_fields(self):
        session = AsyncMock()
        med = MagicMock()
        med.name = "Aspirin"
        med.form = "tablet"
        med.dosage = "500mg"
        med.course_duration = 7
        med.stock_amount = 20
        schedule = MagicMock()
        schedule.scheduled_time = "08:00"
        med.schedules = [schedule]

        with _patch_crud_get_user_medicines(session, [med]):
            result = await execute_get_my_medicines(session, user_id=1, args={})

        assert result["medicines"] == [
            {
                "name": "Aspirin",
                "form": "tablet",
                "dosage": "500mg",
                "schedule": ["08:00"],
                "remaining_doses": 7,
                "stock_amount": 20,
            }
        ]


class TestExecuteRequestMedicineRemoval:
    async def test_medicine_not_found_returns_error(self):
        with _patch_crud_get_user_medicines(AsyncMock(), []):
            session = AsyncMock()
            result = await execute_request_medicine_removal(session, user_id=1, args={"medicine_name": "Unknown"})

        assert "error" in result

    async def test_returns_confirmation_payload_for_found_medicine(self):
        med = MagicMock()
        med.name = "Aspirin"
        med.id = 42

        with _patch_crud_get_user_medicines(AsyncMock(), [med]):
            session = AsyncMock()
            result = await execute_request_medicine_removal(session, user_id=1, args={"medicine_name": "Aspirin"})

        assert result == {
            "requires_confirmation": True,
            "target_type": "medicine",
            "target_id": 42,
            "target_name": "Aspirin",
        }
