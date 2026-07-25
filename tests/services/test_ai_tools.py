"""
Tests for services/ai_tools.py.

Split into two groups:
1. Pure helper functions (_parse_date_flexible, _to_int) — no mocking needed.
2. execute_* functions — these call `database.crud`, so crud is mocked via
   monkeypatch/AsyncMock rather than hitting a real DB. The goal here is to
   verify the VALIDATION logic (bounds checking, malformed input handling),
   not SQLAlchemy behavior itself.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

from services.ai_tools import (
    _find_medicine,
    _find_prescription,
    _parse_date_flexible,
    _to_int,
    execute_add_medicine_reminder,
    execute_add_prescription_entry,
    execute_get_my_medicines,
    execute_get_my_prescriptions,
    execute_mark_prescription_bought,
    execute_request_medicine_removal,
    execute_request_prescription_removal,
    execute_tool,
    execute_update_medicine,
    execute_update_prescription,
)


class TestParseDateFlexible:
    def test_parses_dd_mm_yy(self):
        assert _parse_date_flexible("15.03.26") == date(2026, 3, 15)

    def test_parses_dd_mm_yyyy(self):
        assert _parse_date_flexible("15.03.2026") == date(2026, 3, 15)

    def test_parses_iso_format(self):
        assert _parse_date_flexible("2026-03-15") == date(2026, 3, 15)

    def test_strips_whitespace(self):
        assert _parse_date_flexible("  15.03.26  ") == date(2026, 3, 15)

    def test_invalid_format_returns_none(self):
        assert _parse_date_flexible("not a date") is None

    def test_empty_string_returns_none(self):
        assert _parse_date_flexible("") is None

    def test_impossible_date_returns_none(self):
        assert _parse_date_flexible("32.13.26") is None


class TestToInt:
    def test_converts_valid_string(self):
        assert _to_int("42") == 42

    def test_converts_valid_int(self):
        assert _to_int(42) == 42

    def test_none_returns_none(self):
        assert _to_int(None) is None

    def test_non_numeric_string_returns_none(self):
        assert _to_int("abc") is None

    def test_below_min_value_returns_none(self):
        assert _to_int(5, min_value=10) is None

    def test_above_max_value_returns_none(self):
        assert _to_int(500, max_value=100) is None

    def test_within_bounds_passes(self):
        assert _to_int(50, min_value=1, max_value=100) == 50

    def test_boundary_values_are_inclusive(self):
        assert _to_int(1, min_value=1, max_value=365) == 1
        assert _to_int(365, min_value=1, max_value=365) == 365

    def test_float_string_returns_none(self):
        # int("3.5") raises ValueError — this guards against a model
        # passing a float where course_duration expects a plain int
        assert _to_int("3.5") is None


class TestFindMedicine:
    async def test_exact_name_match(self):
        session = AsyncMock()
        med = MagicMock(name="Aspirin")
        med.name = "Aspirin"

        with _patch_crud_get_user_medicines(session, [med]):
            result = await _find_medicine(session, user_id=1, identifier="Aspirin")
            assert result is med

    async def test_case_insensitive_exact_match(self):
        session = AsyncMock()
        med = MagicMock()
        med.name = "Aspirin"

        with _patch_crud_get_user_medicines(session, [med]):
            result = await _find_medicine(session, user_id=1, identifier="aspirin")
            assert result is med

    async def test_falls_back_to_partial_match_when_unique(self):
        session = AsyncMock()
        med = MagicMock()
        med.name = "Aspirin Forte 500mg"

        with _patch_crud_get_user_medicines(session, [med]):
            result = await _find_medicine(session, user_id=1, identifier="aspirin")
            assert result is med

    async def test_ambiguous_partial_match_returns_none(self):
        session = AsyncMock()
        med1, med2 = MagicMock(), MagicMock()
        med1.name = "Aspirin Forte"
        med2.name = "Aspirin Light"

        with _patch_crud_get_user_medicines(session, [med1, med2]):
            result = await _find_medicine(session, user_id=1, identifier="aspirin")
            assert result is None

    async def test_no_match_returns_none(self):
        session = AsyncMock()
        med = MagicMock()
        med.name = "Ibuprofen"

        with _patch_crud_get_user_medicines(session, [med]):
            result = await _find_medicine(session, user_id=1, identifier="paracetamol")
            assert result is None


def _patch_crud_get_user_medicines(session, medicines):
    """Context manager that patches database.crud.get_user_medicines for the duration of a test."""
    import database.crud as crud_module

    class _Patch:
        def __enter__(self):
            self._original = crud_module.get_user_medicines
            crud_module.get_user_medicines = AsyncMock(return_value=medicines)
            return self

        def __exit__(self, *args):
            crud_module.get_user_medicines = self._original

    return _Patch()


def _patch_crud_get_user_prescriptions(prescriptions):
    import database.crud as crud_module

    class _Patch:
        def __enter__(self):
            self._original = crud_module.get_user_prescriptions
            crud_module.get_user_prescriptions = AsyncMock(return_value=prescriptions)
            return self

        def __exit__(self, *args):
            crud_module.get_user_prescriptions = self._original

    return _Patch()


class TestFindPrescription:
    async def test_exact_match(self):
        session = AsyncMock()
        presc = MagicMock()
        presc.medicine_name = "Amoxicillin"

        with _patch_crud_get_user_prescriptions([presc]):
            result = await _find_prescription(session, user_id=1, identifier="Amoxicillin")
            assert result is presc

    async def test_ambiguous_returns_none(self):
        session = AsyncMock()
        p1, p2 = MagicMock(), MagicMock()
        p1.medicine_name = "Amoxicillin 250mg"
        p2.medicine_name = "Amoxicillin 500mg"

        with _patch_crud_get_user_prescriptions([p1, p2]):
            result = await _find_prescription(session, user_id=1, identifier="amoxicillin")
            assert result is None


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


class TestExecuteTool:
    async def test_unknown_tool_returns_error(self):
        session = AsyncMock()
        result = await execute_tool("nonexistent_tool", session, user_id=1)
        assert "error" in result
        assert "Unknown tool" in result["error"]

    async def test_exception_in_executor_rolls_back_and_returns_error(self, monkeypatch):
        async def broken_executor(session, user_id, args):
            raise RuntimeError("boom")

        from services import ai_tools

        monkeypatch.setitem(ai_tools.TOOL_EXECUTORS, "get_my_medicines", broken_executor)

        session = AsyncMock()
        result = await execute_tool("get_my_medicines", session, user_id=1)

        session.rollback.assert_awaited_once()
        assert "error" in result


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
