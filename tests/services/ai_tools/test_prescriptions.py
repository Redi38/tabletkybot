"""
Tests for services/ai_tools/prescriptions.py — prescription-related tool
executors. crud is mocked via monkeypatch/AsyncMock rather than hitting a
real DB. The goal is to verify VALIDATION logic (bounds checking, malformed
input handling), not SQLAlchemy behavior itself.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

from services.ai_tools import (
    execute_add_prescription_entry,
    execute_get_my_prescriptions,
    execute_mark_prescription_bought,
    execute_request_prescription_removal,
    execute_update_prescription,
)

from ._crud_patches import _patch_crud_get_user_prescriptions


class TestExecuteAddPrescriptionEntry:
    async def test_rejects_unparseable_dates(self):
        session = AsyncMock()
        result = await execute_add_prescription_entry(
            session,
            user_id=1,
            args={
                "medicine_name": "Amoxicillin",
                "issued_date": "garbage",
                "valid_from_date": "garbage",
                "duration_days": 30,
            },
        )
        assert "error" in result

    async def test_rejects_duration_not_30_or_60(self):
        session = AsyncMock()
        result = await execute_add_prescription_entry(
            session,
            user_id=1,
            args={
                "medicine_name": "Amoxicillin",
                "issued_date": "01.01.26",
                "valid_from_date": "01.01.26",
                "duration_days": 45,
            },
        )
        assert "error" in result

    async def test_computes_expires_at_from_valid_from_plus_duration(self, monkeypatch):
        import database.crud as crud_module

        added_kwargs = {}

        async def fake_add_prescription(**kwargs):
            added_kwargs.update(kwargs)
            presc = MagicMock()
            presc.medicine_name = kwargs["medicine_name"]
            return presc

        monkeypatch.setattr(crud_module, "add_prescription", fake_add_prescription)

        session = AsyncMock()
        result = await execute_add_prescription_entry(
            session,
            user_id=1,
            args={
                "medicine_name": "Amoxicillin",
                "issued_date": "01.01.26",
                "valid_from_date": "01.01.26",
                "duration_days": 30,
            },
        )

        assert result["success"] is True
        assert added_kwargs["expires_at"] == date(2026, 1, 31)


class TestExecuteAddPrescriptionEntryExtra:
    async def test_rejects_invalid_max_quantity(self):
        session = AsyncMock()
        result = await execute_add_prescription_entry(
            session,
            user_id=1,
            args={
                "medicine_name": "Amoxicillin",
                "issued_date": "01.01.26",
                "valid_from_date": "01.01.26",
                "duration_days": 30,
                "max_quantity": -5,
            },
        )
        assert "error" in result

    async def test_falls_back_to_default_reminder_days_when_invalid(self, monkeypatch):
        import database.crud as crud_module

        added_kwargs = {}

        async def fake_add_prescription(**kwargs):
            added_kwargs.update(kwargs)
            presc = MagicMock()
            presc.medicine_name = kwargs["medicine_name"]
            return presc

        monkeypatch.setattr(crud_module, "add_prescription", fake_add_prescription)

        session = AsyncMock()
        result = await execute_add_prescription_entry(
            session,
            user_id=1,
            args={
                "medicine_name": "Amoxicillin",
                "issued_date": "01.01.26",
                "valid_from_date": "01.01.26",
                "duration_days": 30,
                "reminder_days_before": "not-a-number",
            },
        )

        assert result["success"] is True
        assert added_kwargs["reminder_days_before"] == 3


class TestExecuteMarkPrescriptionBought:
    async def test_rejects_amount_exceeding_remaining_quantity(self):
        presc = MagicMock()
        presc.medicine_name = "Amoxicillin"
        presc.max_quantity = 10
        presc.purchased_quantity = 8

        with _patch_crud_get_user_prescriptions([presc]):
            session = AsyncMock()
            result = await execute_mark_prescription_bought(
                session,
                user_id=1,
                args={
                    "medicine_name": "Amoxicillin",
                    "amount": 5,
                },
            )
            assert "error" in result
            assert "remaining" in result["error"] or "exceeded" in result["error"].lower()

    async def test_rejects_invalid_amount(self):
        presc = MagicMock()
        presc.medicine_name = "Amoxicillin"
        presc.max_quantity = None

        with _patch_crud_get_user_prescriptions([presc]):
            session = AsyncMock()
            result = await execute_mark_prescription_bought(
                session,
                user_id=1,
                args={
                    "medicine_name": "Amoxicillin",
                    "amount": "not-a-number",
                },
            )
            assert "error" in result


class TestExecuteMarkPrescriptionBoughtExtra:
    async def test_prescription_not_found_returns_error(self):
        with _patch_crud_get_user_prescriptions([]):
            session = AsyncMock()
            result = await execute_mark_prescription_bought(
                session, user_id=1, args={"medicine_name": "Unknown", "amount": 5}
            )

        assert "error" in result

    async def test_succeeds_within_remaining_quantity(self, monkeypatch):
        import database.crud as crud_module

        presc = MagicMock()
        presc.medicine_name = "Amoxicillin"
        presc.id = 1
        presc.max_quantity = 10
        presc.purchased_quantity = 3

        monkeypatch.setattr(
            crud_module, "mark_prescription_purchased", AsyncMock(return_value={"purchased_quantity": 8})
        )

        with _patch_crud_get_user_prescriptions([presc]):
            session = AsyncMock()
            result = await execute_mark_prescription_bought(
                session, user_id=1, args={"medicine_name": "Amoxicillin", "amount": 5}
            )

        assert result == {"success": True, "purchased_quantity": 8}

    async def test_succeeds_when_no_max_quantity_limit(self, monkeypatch):
        import database.crud as crud_module

        presc = MagicMock()
        presc.medicine_name = "Amoxicillin"
        presc.id = 1
        presc.max_quantity = None

        monkeypatch.setattr(
            crud_module, "mark_prescription_purchased", AsyncMock(return_value={"purchased_quantity": 5})
        )

        with _patch_crud_get_user_prescriptions([presc]):
            session = AsyncMock()
            result = await execute_mark_prescription_bought(
                session, user_id=1, args={"medicine_name": "Amoxicillin", "amount": 5}
            )

        assert result["success"] is True


class TestExecuteGetMyPrescriptions:
    async def test_returns_empty_list_with_note_when_none(self):
        with _patch_crud_get_user_prescriptions([]):
            session = AsyncMock()
            result = await execute_get_my_prescriptions(session, user_id=1, args={})

        assert result["prescriptions"] == []
        assert "note" in result

    async def test_returns_serialized_prescription_fields(self):
        presc = MagicMock()
        presc.medicine_name = "Amoxicillin"
        presc.valid_from = date(2026, 1, 1)
        presc.expires_at = date(2026, 1, 31)
        presc.max_quantity = 10
        presc.purchased_quantity = 3
        presc.is_fully_purchased = False

        with _patch_crud_get_user_prescriptions([presc]):
            session = AsyncMock()
            result = await execute_get_my_prescriptions(session, user_id=1, args={})

        assert result["prescriptions"] == [
            {
                "medicine_name": "Amoxicillin",
                "valid_from": "2026-01-01",
                "expires_at": "2026-01-31",
                "max_quantity": 10,
                "purchased_quantity": 3,
                "is_fully_purchased": False,
            }
        ]


class TestExecuteUpdatePrescription:
    async def test_prescription_not_found_returns_error(self):
        with _patch_crud_get_user_prescriptions([]):
            session = AsyncMock()
            result = await execute_update_prescription(
                session, user_id=1, args={"medicine_name": "Unknown", "field": "notes", "value": "x"}
            )

        assert "error" in result

    async def test_rejects_missing_field(self):
        presc = MagicMock()
        presc.medicine_name = "Amoxicillin"
        presc.id = 1

        with _patch_crud_get_user_prescriptions([presc]):
            session = AsyncMock()
            result = await execute_update_prescription(
                session, user_id=1, args={"medicine_name": "Amoxicillin", "field": None, "value": "x"}
            )

        assert "error" in result

    async def test_rejects_max_quantity_out_of_bounds(self):
        presc = MagicMock()
        presc.medicine_name = "Amoxicillin"
        presc.id = 1

        with _patch_crud_get_user_prescriptions([presc]):
            session = AsyncMock()
            result = await execute_update_prescription(
                session,
                user_id=1,
                args={"medicine_name": "Amoxicillin", "field": "max_quantity", "value": "not-a-number"},
            )

        assert "error" in result

    async def test_rejects_reminder_days_before_out_of_bounds(self):
        presc = MagicMock()
        presc.medicine_name = "Amoxicillin"
        presc.id = 1

        with _patch_crud_get_user_prescriptions([presc]):
            session = AsyncMock()
            result = await execute_update_prescription(
                session,
                user_id=1,
                args={"medicine_name": "Amoxicillin", "field": "reminder_days_before", "value": "999"},
            )

        assert "error" in result

    async def test_updates_notes_field_and_truncates(self, monkeypatch):
        import database.crud as crud_module

        presc = MagicMock()
        presc.medicine_name = "Amoxicillin"
        presc.id = 1
        update_call = {}

        async def fake_update_field(session, prescription_id, field, value):
            update_call.update({"prescription_id": prescription_id, "field": field, "value": value})

        monkeypatch.setattr(crud_module, "update_prescription_field", fake_update_field)

        with _patch_crud_get_user_prescriptions([presc]):
            session = AsyncMock()
            result = await execute_update_prescription(
                session, user_id=1, args={"medicine_name": "Amoxicillin", "field": "notes", "value": "Take with food"}
            )

        assert result["success"] is True
        assert update_call == {"prescription_id": 1, "field": "notes", "value": "Take with food"}

    async def test_updates_max_quantity_within_bounds(self, monkeypatch):
        import database.crud as crud_module

        presc = MagicMock()
        presc.medicine_name = "Amoxicillin"
        presc.id = 1

        monkeypatch.setattr(crud_module, "update_prescription_field", AsyncMock())

        with _patch_crud_get_user_prescriptions([presc]):
            session = AsyncMock()
            result = await execute_update_prescription(
                session, user_id=1, args={"medicine_name": "Amoxicillin", "field": "max_quantity", "value": "20"}
            )

        assert result["success"] is True
        assert result["new_value"] == 20


class TestExecuteRequestPrescriptionRemoval:
    async def test_prescription_not_found_returns_error(self):
        with _patch_crud_get_user_prescriptions([]):
            session = AsyncMock()
            result = await execute_request_prescription_removal(session, user_id=1, args={"medicine_name": "Unknown"})

        assert "error" in result

    async def test_returns_confirmation_payload_for_found_prescription(self):
        presc = MagicMock()
        presc.medicine_name = "Amoxicillin"
        presc.id = 7

        with _patch_crud_get_user_prescriptions([presc]):
            session = AsyncMock()
            result = await execute_request_prescription_removal(
                session, user_id=1, args={"medicine_name": "Amoxicillin"}
            )

        assert result == {
            "requires_confirmation": True,
            "target_type": "prescription",
            "target_id": 7,
            "target_name": "Amoxicillin",
        }
