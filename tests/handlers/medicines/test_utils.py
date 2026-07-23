"""
Tests for handlers/medicines/utils.py: the pure parsing/validation helpers
shared across the add/edit/extend/restock flows, plus the two callback
context helpers (_safe_edit_text, _valid_medicine_ctx, _base_ctx).
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.medicines.utils import (
    _base_ctx,
    _safe_edit_text,
    _valid_medicine_ctx,
    is_valid_time,
    parse_int,
    parse_times,
)


class TestIsValidTime:
    @pytest.mark.parametrize("value", ["00:00", "23:59", "9:05", "12:30"])
    def test_accepts_valid_times(self, value):
        assert is_valid_time(value) is True

    @pytest.mark.parametrize("value", ["24:00", "12:60", "-1:30", "not-a-time", "12", "12:30:00", ""])
    def test_rejects_invalid_times(self, value):
        assert is_valid_time(value) is False


class TestParseTimes:
    def test_parses_comma_separated_times(self):
        assert parse_times("08:00, 20:00") == ["08:00", "20:00"]

    def test_parses_semicolon_separated_times(self):
        assert parse_times("08:00; 20:00") == ["08:00", "20:00"]

    def test_returns_none_if_any_time_is_invalid(self):
        # All-or-nothing: one bad entry invalidates the whole batch rather
        # than silently dropping it.
        assert parse_times("08:00, 25:00") is None

    def test_returns_none_for_empty_string(self):
        assert parse_times("") is None

    def test_single_time(self):
        assert parse_times("09:30") == ["09:30"]


class TestParseInt:
    def test_parses_a_positive_int(self):
        assert parse_int("5") == 5

    def test_parses_zero(self):
        assert parse_int("0") == 0

    def test_rejects_negative_numbers(self):
        assert parse_int("-1") is None

    def test_rejects_non_numeric_input(self):
        assert parse_int("abc") is None

    def test_rejects_float_strings(self):
        assert parse_int("5.5") is None


class TestSafeEditText:
    async def test_calls_edit_text_normally(self):
        msg = create_autospec(Message, instance=True)
        msg.edit_text = AsyncMock()

        await _safe_edit_text(msg, "hello", parse_mode="HTML")

        msg.edit_text.assert_awaited_once_with("hello", parse_mode="HTML")

    async def test_swallows_message_not_modified_error(self):
        msg = create_autospec(Message, instance=True)
        msg.edit_text = AsyncMock(
            side_effect=TelegramBadRequest(method=MagicMock(), message="Bad Request: message is not modified")
        )

        # Must not raise — a duplicate tap producing identical content is expected.
        await _safe_edit_text(msg, "hello")

    async def test_reraises_other_telegram_bad_request_errors(self):
        msg = create_autospec(Message, instance=True)
        msg.edit_text = AsyncMock(
            side_effect=TelegramBadRequest(method=MagicMock(), message="Bad Request: message to edit not found")
        )

        with pytest.raises(TelegramBadRequest):
            await _safe_edit_text(msg, "hello")


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
        call, message = _fake_call(1, "some_data")

        result = await _base_ctx(call, db_session)

        assert result is not None
        assert result[0] is message
        assert result[1] == "ua"  # default language

    async def test_returns_none_when_no_from_user(self, db_session):
        call, _ = _fake_call(1, "some_data")
        call.from_user = None

        result = await _base_ctx(call, db_session)

        assert result is None


class TestValidMedicineCtx:
    async def test_returns_medicine_when_found(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        medicine = await crud.add_medicine(
            db_session,
            user_id=1,
            name="Ibuprofen",
            form="tablets",
            dosage="200mg",
            schedules_list=["09:00"],
            course_duration=5,
        )
        await db_session.commit()
        call, message = _fake_call(1, f"med_edit_{medicine.id}")

        result = await _valid_medicine_ctx(call, db_session)

        assert result is not None
        msg, lang, medicine_id, med = result
        assert medicine_id == medicine.id
        assert med.id == medicine.id

    async def test_answers_with_alert_when_medicine_not_found(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, _ = _fake_call(1, "med_edit_999")

        result = await _valid_medicine_ctx(call, db_session)

        assert result is None
        call.answer.assert_awaited_once()
        assert call.answer.call_args.kwargs.get("show_alert") is True

    async def test_returns_none_for_malformed_callback_data(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, _ = _fake_call(1, "med_edit_not_a_number")

        result = await _valid_medicine_ctx(call, db_session)

        assert result is None
