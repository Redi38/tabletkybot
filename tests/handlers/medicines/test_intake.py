"""
Tests for handlers/medicines/intake.py: the mark_taken_now self-service
handler and the shared _send_take_result_followup / _prompt_restock_before_take
helpers it shares with the take_/skip_ reminder-button handler.
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

from aiogram.types import CallbackQuery, Message

from database import crud
from handlers.medicines.intake import mark_taken_now


def _fake_call(user_id: int, medicine_id: int, message_id: int = 1):
    message = create_autospec(Message, instance=True)
    message.message_id = message_id
    message.edit_text = AsyncMock()
    message.answer = AsyncMock()

    call = create_autospec(CallbackQuery, instance=True)
    call.data = f"mark_taken_now_{medicine_id}"
    call.from_user = MagicMock(id=user_id, username="tester")
    call.answer = AsyncMock()
    call.message = message

    return call, message


def _fake_state():
    state = MagicMock()
    state.update_data = AsyncMock()
    return state


async def _add_medicine(session, user_id=1, stock_amount=None, low_stock_threshold=5, course_duration=10):
    await crud.get_or_create_user(session, user_id, "tester", "Test User")
    medicine = await crud.add_medicine(
        session,
        user_id=user_id,
        name="Ibuprofen",
        form="tablets",
        dosage="200mg",
        schedules_list=["09:00"],
        course_duration=course_duration,
        stock_amount=stock_amount,
        low_stock_threshold=low_stock_threshold,
    )
    await session.commit()
    return medicine


class TestMarkTakenNowHappyPath:
    async def test_records_a_taken_dose(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session, course_duration=10)
        call, message = _fake_call(user_id=1, medicine_id=medicine.id)
        state = _fake_state()

        await mark_taken_now(call, state, db_session)

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.course_duration == 9  # one dose consumed
        message.edit_text.assert_awaited_once()

    async def test_decrements_stock_when_tracked(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session, stock_amount=10, course_duration=10)
        call, message = _fake_call(user_id=1, medicine_id=medicine.id)
        state = _fake_state()

        await mark_taken_now(call, state, db_session)

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.stock_amount == 9

    async def test_cancels_any_pending_repeat_reminder(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session, course_duration=10)
        call, message = _fake_call(user_id=1, medicine_id=medicine.id)
        state = _fake_state()

        with patch("handlers.medicines.intake.cancel_repeat_reminder", AsyncMock()) as mock_cancel:
            await mark_taken_now(call, state, db_session)

        mock_cancel.assert_awaited_once_with(1, medicine.id)

    async def test_respects_action_lock(self, db_session, mock_redis):
        """A second rapid press (e.g. double-tap) while the first is still
        being processed must be ignored, not double-record the dose."""
        medicine = await _add_medicine(db_session, course_duration=10)
        call, message = _fake_call(user_id=1, medicine_id=medicine.id)
        state = _fake_state()

        with patch("handlers.medicines.intake.acquire_action_lock", AsyncMock(return_value=False)):
            await mark_taken_now(call, state, db_session)

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.course_duration == 10  # unchanged — nothing recorded
        message.edit_text.assert_not_awaited()


class TestMarkTakenNowZeroStock:
    async def test_redirects_to_restock_flow_instead_of_recording(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session, stock_amount=0, course_duration=10)
        call, message = _fake_call(user_id=1, medicine_id=medicine.id)
        state = _fake_state()

        await mark_taken_now(call, state, db_session)

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.course_duration == 10  # not recorded — redirected instead
        state.update_data.assert_awaited_once_with(medicine_id=medicine.id, lang="ua")
        message.edit_text.assert_awaited_once()
        # The restock prompt keyboard should offer both options
        _, kwargs = message.edit_text.call_args
        callback_datas = [btn.callback_data for row in kwargs["reply_markup"].inline_keyboard for btn in row]
        assert f"restock_yes_{medicine.id}" in callback_datas
        assert f"restock_no_{medicine.id}" in callback_datas


class TestMarkTakenNowCourseFinished:
    async def test_shows_continue_or_finish_prompt_on_last_dose(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session, course_duration=1)
        call, message = _fake_call(user_id=1, medicine_id=medicine.id)
        state = _fake_state()

        await mark_taken_now(call, state, db_session)

        message.edit_text.assert_awaited_once()
        _, kwargs = message.edit_text.call_args
        callback_datas = [btn.callback_data for row in kwargs["reply_markup"].inline_keyboard for btn in row]
        assert f"med_extend_ask_{medicine.id}" in callback_datas
        assert f"med_archive_confirm_{medicine.id}" in callback_datas


class TestMarkTakenNowStockAlerts:
    async def test_shows_empty_stock_alert_when_last_unit_taken(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session, stock_amount=1, course_duration=10)
        call, message = _fake_call(user_id=1, medicine_id=medicine.id)
        state = _fake_state()

        await mark_taken_now(call, state, db_session)

        message.answer.assert_awaited_once()
        _, kwargs = message.answer.call_args
        callback_datas = [btn.callback_data for row in kwargs["reply_markup"].inline_keyboard for btn in row]
        assert f"restock_ask_{medicine.id}" in callback_datas
        assert f"med_archive_confirm_{medicine.id}" in callback_datas

    async def test_shows_low_stock_alert_below_threshold(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session, stock_amount=3, low_stock_threshold=5, course_duration=10)
        call, message = _fake_call(user_id=1, medicine_id=medicine.id)
        state = _fake_state()

        await mark_taken_now(call, state, db_session)

        message.answer.assert_awaited_once()
        _, kwargs = message.answer.call_args
        callback_datas = [btn.callback_data for row in kwargs["reply_markup"].inline_keyboard for btn in row]
        assert f"restock_ask_{medicine.id}" in callback_datas

    async def test_no_stock_alert_when_stock_not_tracked(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session, stock_amount=None, course_duration=10)
        call, message = _fake_call(user_id=1, medicine_id=medicine.id)
        state = _fake_state()

        await mark_taken_now(call, state, db_session)

        message.answer.assert_not_awaited()

    async def test_no_stock_alert_when_comfortably_above_threshold(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session, stock_amount=50, low_stock_threshold=5, course_duration=10)
        call, message = _fake_call(user_id=1, medicine_id=medicine.id)
        state = _fake_state()

        await mark_taken_now(call, state, db_session)

        message.answer.assert_not_awaited()


class TestMarkTakenNowUnknownMedicine:
    async def test_shows_alert_and_does_nothing_for_missing_medicine(self, db_session, mock_redis):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(user_id=1, medicine_id=99999)
        state = _fake_state()

        await mark_taken_now(call, state, db_session)

        assert call.answer.await_count == 2  # unconditional ack + the "not found" alert
        last_args, last_kwargs = call.answer.call_args
        assert last_kwargs.get("show_alert") is True
        message.edit_text.assert_not_awaited()
