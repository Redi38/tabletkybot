"""
Tests for services/ai_tools/helpers.py — pure functions and DB-lookup
helpers (_parse_date_flexible, _to_int, _find_medicine, _find_prescription).
No crud writes are involved here, only reads via monkeypatched crud.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

from services.ai_tools import _find_medicine, _find_prescription, _parse_date_flexible, _to_int

from ._crud_patches import _patch_crud_get_user_medicines, _patch_crud_get_user_prescriptions


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
