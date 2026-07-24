"""
Tests for handlers/ai_agent.py: the removal-confirm keyboard builder, the
text/voice fallback entry points into the AI agent, and the resulting
archive/delete/cancel confirmation callback.
"""

from unittest.mock import AsyncMock, MagicMock, create_autospec

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import handlers.ai_agent as ai_agent_module
from database import crud
from handlers.ai_agent import build_removal_confirm_kb, fallback_handler, handle_ai_action_confirm, handle_voice


def _fake_state(current_state=None):
    state = create_autospec(FSMContext, instance=True)
    state.get_state = AsyncMock(return_value=current_state)
    return state


def _fake_message(text: str | None = None, user_id: int = 1, voice=None):
    message = create_autospec(Message, instance=True)
    message.text = text
    message.voice = voice
    message.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    message.chat = MagicMock(id=user_id)
    message.answer = AsyncMock()
    return message


def _fake_call(user_id: int, data: str):
    message = create_autospec(Message, instance=True)
    message.edit_text = AsyncMock()

    call = create_autospec(CallbackQuery, instance=True)
    call.data = data
    call.from_user = MagicMock(id=user_id, username="tester", full_name="Test User")
    call.answer = AsyncMock()
    call.message = message
    return call, message


class TestBuildRemovalConfirmKb:
    def test_medicine_confirmation_uses_med_prefix(self):
        keyboard = build_removal_confirm_kb({"target_type": "medicine", "target_id": 42}, "en")

        callback_data = {btn.callback_data for row in keyboard.inline_keyboard for btn in row}
        assert "ai_act_med_archive_42" in callback_data
        assert "ai_act_med_delete_42" in callback_data
        assert "ai_act_cancel" in callback_data

    def test_prescription_confirmation_uses_presc_prefix(self):
        keyboard = build_removal_confirm_kb({"target_type": "prescription", "target_id": 7}, "en")

        callback_data = {btn.callback_data for row in keyboard.inline_keyboard for btn in row}
        assert "ai_act_presc_archive_7" in callback_data
        assert "ai_act_presc_delete_7" in callback_data


class TestFallbackHandler:
    async def test_skips_when_fsm_state_is_active(self, db_session):
        message = _fake_message(text="hello")
        state = _fake_state(current_state="SomeState:waiting")

        await fallback_handler(message, state, db_session, config=MagicMock(), bot=AsyncMock())

        message.answer.assert_not_awaited()

    async def test_skips_when_no_text(self, db_session):
        message = _fake_message(text=None)
        state = _fake_state()

        await fallback_handler(message, state, db_session, config=MagicMock(), bot=AsyncMock())

        message.answer.assert_not_awaited()

    async def test_skips_when_no_from_user(self, db_session):
        message = _fake_message(text="hello")
        message.from_user = None
        state = _fake_state()

        await fallback_handler(message, state, db_session, config=MagicMock(), bot=AsyncMock())

        message.answer.assert_not_awaited()

    async def test_sends_the_ai_response_back_to_the_user(self, db_session, monkeypatch):
        monkeypatch.setattr(
            ai_agent_module, "get_ai_agent_response", AsyncMock(return_value=("Hi there!", "test-model", None))
        )
        message = _fake_message(text="hello")
        state = _fake_state()

        await fallback_handler(message, state, db_session, config=MagicMock(), bot=AsyncMock())

        message.answer.assert_awaited_once()
        assert "Hi there" in message.answer.call_args.args[0]

    async def test_stores_the_conversation_in_chat_history(self, db_session, monkeypatch):
        monkeypatch.setattr(
            ai_agent_module, "get_ai_agent_response", AsyncMock(return_value=("Hi there!", "test-model", None))
        )
        message = _fake_message(text="hello", user_id=1)
        state = _fake_state()

        await fallback_handler(message, state, db_session, config=MagicMock(), bot=AsyncMock())

        history = await crud.get_chat_history(db_session, 1, limit=10)
        assert len(history) == 2

    async def test_confirmation_response_shows_archive_delete_prompt_instead(self, db_session, monkeypatch):
        confirmation = {"target_type": "medicine", "target_id": 5, "target_name": "Ibuprofen"}
        monkeypatch.setattr(
            ai_agent_module, "get_ai_agent_response", AsyncMock(return_value=("", "test-model", confirmation))
        )
        message = _fake_message(text="delete my ibuprofen")
        state = _fake_state()

        await fallback_handler(message, state, db_session, config=MagicMock(), bot=AsyncMock())

        message.answer.assert_awaited_once()
        assert message.answer.call_args.kwargs["reply_markup"] is not None


class TestHandleVoice:
    async def test_skips_when_fsm_state_is_active(self, db_session):
        message = _fake_message(voice=MagicMock(file_id="abc"))
        state = _fake_state(current_state="SomeState:waiting")

        await handle_voice(message, state, db_session, config=MagicMock(), bot=AsyncMock())

        message.answer.assert_not_awaited()

    async def test_skips_when_no_voice(self, db_session):
        message = _fake_message(voice=None)
        state = _fake_state()

        await handle_voice(message, state, db_session, config=MagicMock(), bot=AsyncMock())

        message.answer.assert_not_awaited()

    async def test_transcription_failure_shows_error(self, db_session, monkeypatch):
        monkeypatch.setattr(
            ai_agent_module, "download_telegram_file", AsyncMock(side_effect=RuntimeError("network down"))
        )
        message = _fake_message(voice=MagicMock(file_id="abc"))
        state = _fake_state()

        await handle_voice(message, state, db_session, config=MagicMock(), bot=AsyncMock())

        message.answer.assert_awaited_once()

    async def test_empty_transcript_shows_error(self, db_session, monkeypatch):
        monkeypatch.setattr(ai_agent_module, "download_telegram_file", AsyncMock(return_value=b"fake-audio"))
        monkeypatch.setattr(ai_agent_module, "transcribe_voice", AsyncMock(return_value=""))
        message = _fake_message(voice=MagicMock(file_id="abc"))
        state = _fake_state()

        await handle_voice(message, state, db_session, config=MagicMock(), bot=AsyncMock())

        message.answer.assert_awaited_once()

    async def test_successful_transcription_feeds_the_ai_agent(self, db_session, monkeypatch):
        monkeypatch.setattr(ai_agent_module, "download_telegram_file", AsyncMock(return_value=b"fake-audio"))
        monkeypatch.setattr(ai_agent_module, "transcribe_voice", AsyncMock(return_value="what should I take today"))
        monkeypatch.setattr(
            ai_agent_module, "get_ai_agent_response", AsyncMock(return_value=("You should take X", "model", None))
        )
        message = _fake_message(voice=MagicMock(file_id="abc"))
        state = _fake_state()

        await handle_voice(message, state, db_session, config=MagicMock(), bot=AsyncMock())

        message.answer.assert_awaited_once()
        assert "You should take X" in message.answer.call_args.args[0]


async def _add_medicine(db_session, user_id=1, name="Ibuprofen"):
    await crud.get_or_create_user(db_session, user_id, "tester", "Test User")
    medicine = await crud.add_medicine(
        db_session,
        user_id=user_id,
        name=name,
        form="tablets",
        dosage="200mg",
        schedules_list=["09:00"],
        course_duration=5,
    )
    await db_session.commit()
    return medicine


class TestHandleAiActionConfirm:
    async def test_cancel_shows_cancelled_message(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "ai_act_cancel")

        await handle_ai_action_confirm(call, db_session)

        message.edit_text.assert_awaited_once()
        call.answer.assert_awaited_once()

    async def test_archives_a_medicine(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session)
        call, message = _fake_call(1, f"ai_act_med_archive_{medicine.id}")

        await handle_ai_action_confirm(call, db_session)

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.is_active is False
        message.edit_text.assert_awaited_once()

    async def test_deletes_a_medicine(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session)
        call, message = _fake_call(1, f"ai_act_med_delete_{medicine.id}")

        await handle_ai_action_confirm(call, db_session)

        assert await crud.get_medicine_by_id(db_session, medicine.id) is None
        message.edit_text.assert_awaited_once()

    async def test_medicine_belonging_to_another_user_is_rejected(self, db_session, mock_redis):
        medicine = await _add_medicine(db_session, user_id=2)
        call, message = _fake_call(1, f"ai_act_med_archive_{medicine.id}")

        await handle_ai_action_confirm(call, db_session)

        refreshed = await crud.get_medicine_by_id(db_session, medicine.id)
        assert refreshed.is_active is True  # unchanged
        call.answer.assert_awaited_once()
        assert call.answer.call_args.kwargs.get("show_alert") is True
        message.edit_text.assert_not_awaited()

    async def test_archives_a_prescription(self, db_session):
        from datetime import date

        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        prescription = await crud.add_prescription(
            db_session,
            user_id=1,
            medicine_name="Amoxicillin",
            valid_from=date(2026, 1, 1),
            expires_at=date(2026, 1, 31),
        )
        await db_session.commit()
        call, message = _fake_call(1, f"ai_act_presc_archive_{prescription.id}")

        await handle_ai_action_confirm(call, db_session)

        refreshed = await crud.get_prescription_by_id(db_session, prescription.id)
        assert refreshed.is_active is False
        message.edit_text.assert_awaited_once()

    async def test_deletes_a_prescription(self, db_session):
        from datetime import date

        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        prescription = await crud.add_prescription(
            db_session,
            user_id=1,
            medicine_name="Amoxicillin",
            valid_from=date(2026, 1, 1),
            expires_at=date(2026, 1, 31),
        )
        await db_session.commit()
        call, message = _fake_call(1, f"ai_act_presc_delete_{prescription.id}")

        await handle_ai_action_confirm(call, db_session)

        assert await crud.get_prescription_by_id(db_session, prescription.id) is None
        message.edit_text.assert_awaited_once()

    async def test_prescription_not_found_shows_alert(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "ai_act_presc_archive_999")

        await handle_ai_action_confirm(call, db_session)

        call.answer.assert_awaited_once()
        assert call.answer.call_args.kwargs.get("show_alert") is True

    async def test_malformed_callback_data_is_ignored(self, db_session):
        await crud.get_or_create_user(db_session, 1, "tester", "Test User")
        call, message = _fake_call(1, "ai_act_med_archive")  # missing target id

        await handle_ai_action_confirm(call, db_session)

        message.edit_text.assert_not_awaited()

    async def test_no_op_without_call_data(self, db_session):
        call, message = _fake_call(1, "ai_act_cancel")
        call.data = None

        await handle_ai_action_confirm(call, db_session)

        message.edit_text.assert_not_awaited()


class TestSendAiAnswer:
    async def test_sends_html_formatted_response(self):
        message = _fake_message()

        await ai_agent_module._send_ai_answer(message, "<b>Hello</b>", "model", "en")

        message.answer.assert_awaited_once()
        assert message.answer.call_args.kwargs["parse_mode"] == "HTML"

    async def test_falls_back_to_plain_text_on_unparseable_entities(self):
        message = _fake_message()
        message.answer = AsyncMock(
            side_effect=[
                TelegramBadRequest(method=MagicMock(), message="can't parse entities: bad tag"),
                None,
            ]
        )

        await ai_agent_module._send_ai_answer(message, "<broken", "model", "en")

        assert message.answer.await_count == 2
        assert message.answer.call_args.kwargs["parse_mode"] is None

    async def test_reraises_unrelated_bad_request_errors(self):
        message = _fake_message()
        message.answer = AsyncMock(side_effect=TelegramBadRequest(method=MagicMock(), message="message too long"))

        try:
            await ai_agent_module._send_ai_answer(message, "text", "model", "en")
            raised = False
        except TelegramBadRequest:
            raised = True
        assert raised
