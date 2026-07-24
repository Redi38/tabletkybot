"""
Tests for handlers/prescriptions/utils.py: date/int/text parsing helpers
shared across the add/edit/restore/stock flows, plus the callback context
helpers (_base_ctx, _valid_prescription_ctx).
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.prescriptions.utils import (
    _base_ctx,
    _valid_prescription_ctx,
    parse_date,
    parse_optional_int,
    parse_optional_text,
    parse_positive_int,
)


class TestParseDate:
    def test_parses_two_digit_year(self):
        assert parse_date("15.03.26") == date(2026, 3, 15)

    def test_parses_four_digit_year(self):
        assert parse_date("15.03.2026") == date(2026, 3, 15)

    def test_strips_surrounding_whitespace(self):
        assert parse_date("  15.03.2026  ") == date(2026, 3, 15)

    def test_returns_none_for_garbage(self):
        assert parse_date("not-a-date") is None

    def test_returns_none_for_wrong_separator(self):
        assert parse_date("15/03/2026") is None

    def test_returns_none_for_invalid_calendar_date(self):
        assert parse_date("31.02.2026") is None


class TestParseOptionalInt:
    def test_dash_means_skip_returns_none(self):
        assert parse_optional_int("-") is None

    def test_parses_a_positive_number(self):
        assert parse_optional_int("30") == 30

    def test_parses_zero(self):
        assert parse_optional_int("0") == 0

    def test_negative_number_is_flagged_as_error_not_skip(self):
        # -1 here is a sentinel for "invalid input", distinct from the "-"
        # skip case which returns None.
        assert parse_optional_int("-5") == -1

    def test_non_numeric_input_is_flagged_as_error(self):
        assert parse_optional_int("abc") == -1


class TestParsePositiveInt:
    def test_parses_a_positive_number(self):
        assert parse_positive_int("30") == 30

    def test_zero_is_invalid_no_skip_option(self):
        assert parse_positive_int("0") is None

    def test_negative_is_invalid(self):
        assert parse_positive_int("-5") is None

    def test_non_numeric_is_invalid(self):
        assert parse_positive_int("abc") is None

    def test_dash_is_not_treated_as_skip_here(self):
        # Unlike parse_optional_int, "-" has no special meaning for a
        # required field — it's just invalid input.
        assert parse_positive_int("-") is None


class TestParseOptionalText:
    def test_dash_means_skip_returns_none(self):
        assert parse_optional_text("-") is None

    def test_returns_stripped_text(self):
        assert parse_optional_text("  Dr. Smith  ") == "Dr. Smith"

    def test_empty_string_is_not_treated_as_dash(self):
        assert parse_optional_text("") == ""


def _fake_call(user_id: int, data: str | None):
    message = create_autospec(Message, instance=True)
    call = create_autospec(CallbackQuery, instance=True)
    call.data = data
    call.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    call.answer = AsyncMock()
    call.message = message
    return call, message


class TestBaseCtx:
    async def test_returns_message_and_language(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "presc_menu")

        result = await _base_ctx(call, db_session)

        assert result is not None
        assert result[0] is message
        assert result[1] == "ua"

    async def test_returns_none_without_from_user(self, db_session):
        call, _ = _fake_call(1, "presc_menu")
        call.from_user = None

        assert await _base_ctx(call, db_session) is None


class TestValidPrescriptionCtx:
    async def test_returns_prescription_when_found(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        prescription = await crud.add_prescription(
            db_session,
            user_id=1,
            medicine_name="Amoxicillin",
            valid_from=date(2026, 1, 1),
            expires_at=date(2026, 12, 31),
        )
        await db_session.commit()
        call, message = _fake_call(1, f"presc_edit_{prescription.id}")

        result = await _valid_prescription_ctx(call, db_session)

        assert result is not None
        msg, lang, prescription_id, presc = result
        assert prescription_id == prescription.id
        assert presc.medicine_name == "Amoxicillin"

    async def test_answers_with_alert_when_prescription_not_found(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, _ = _fake_call(1, "presc_edit_999")

        result = await _valid_prescription_ctx(call, db_session)

        assert result is None
        call.answer.assert_awaited_once()
        assert call.answer.call_args.kwargs.get("show_alert") is True

    async def test_returns_none_for_malformed_callback_data(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, _ = _fake_call(1, "presc_edit_not_a_number")

        assert await _valid_prescription_ctx(call, db_session) is None
